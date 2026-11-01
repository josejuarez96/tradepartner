"""Locked tests for #516: the lot ledger takes journaled adjustments (paper spec
req 13, "Adjustments", and its acceptance bullet "Adjustments in the lot
ledger"; owner decision 2026-10-19 on #516: **long lots only**).

`execution.lots.rebuild` gains four keyword arguments, all defaulted so every
existing call (and every test in `test_lots.py`) is unchanged:

- `adjustments`: the window's `adjustments` rows (`store.journal.adjustments_for`);
- `actions`: a `store.asof.live_actions_as_of(conn, close(S-1))` frame, whose
  `split` rows are applied (the positions ledger's own input, so no look-ahead);
- `through`: the session S the rebuild is stated for. A split with
  `ex_date <= through` and an adjustment with `session <= through` apply;
  later ones do not;
- `symbols`: `security_id` -> broker symbol, for a lot an adjustment opens or
  converts (an `adjustments` row carries no symbol).

What the lock pins, case by case (every value is hand-computed in the test's
docstring):

- **Splits** scale every open long lot of the name: quantity by the ratio,
  total basis and acquisition instant unchanged. A fill on the ex-date is
  already in post-split shares. Disposals stay in the share units of their
  own sale. Realised P&L of a round trip is the same as the split-free one.
- **Cash in lieu** of a split's fractional remainder (`cash_in_lieu`, its
  `quantity` the negative fraction in post-split shares, its `cash` the
  proceeds) is a disposal of that fraction at its journaled cash.
- **Spin-off receipts** open one child lot on the adjustment's session. Its
  basis is zero unless the row's `explanation_json` names the parent and the
  fraction of the parent's basis that moves to the child (`parent_security_id`,
  `basis_allocation`); then the parent's open lots give up that fraction and
  total basis is conserved.
- **Stock mergers** (paper spec amendment #1762, item 5; build line T249b): the
  journal shape is item 5's pair, both rows naming the merger id in
  `explanation_json` (key `merger_id`, this lock's assumption): a
  `corporate_action_cash` row removing the acquiree and a `merger_receipt` row
  adding the acquirer. Paired by the merger id, the acquiree's open lots become
  acquirer lots at the ratio of the two quantities, basis and acquisition
  instant carried, with no disposal. A `corporate_action_cash` naming a merger
  id with no receipt (a cash merger, item 2) stays a disposal at its cash.
- **Carried residue** opens a zero-basis lot on its session that is never a
  wash-sale replacement; **`corporate_action_cash`** closes the name's open
  lots FIFO as one sale at the journaled cash.
- **Short lots**: any corporate action on a name with an open short lot still
  raises `LotLedgerError` (until the short build covers it).
- **Determinism**: the same journal gives the same ledger, whatever order the
  adjustments arrive in.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import count
from typing import Any

import duckdb
import polars as pl
import pytest

from tradepartner.calendar import session_close
from tradepartner.execution import lots
from tradepartner.execution.ledger import from_journal
from tradepartner.execution.lots import LedgerAccount, LotLedgerError, rebuild
from tradepartner.store import schema
from tradepartner.store.asof import live_actions_as_of
from tradepartner.store.db import insert_row
from tradepartner.store.journal import AdjustmentRow, FillRow, OrderedFill, OrderRow

ACCOUNT = LedgerAccount(account_id="paper-1", account_type="paper", owner="self")
_STAMP = datetime(2030, 1, 1, tzinfo=UTC)
_fill_ids = count(1)
_adjustment_ids = count(1)

# XNYS sessions of March 2025 (DST starts 03-09, so the close is 21:00 UTC
# before it and 20:00 UTC after it).
D0303 = date(2025, 3, 3)
D0304 = date(2025, 3, 4)
D0305 = date(2025, 3, 5)
D0306 = date(2025, 3, 6)
D0307 = date(2025, 3, 7)
D0310 = date(2025, 3, 10)
D0312 = date(2025, 3, 12)
D0313 = date(2025, 3, 13)

SHORT = "short"
LONG = "long"


def _at(day: date) -> datetime:
    """A fill instant on `day`: 19:00 UTC is 14:00 or 15:00 in New York."""
    return datetime(day.year, day.month, day.day, 19, tzinfo=UTC)


class Book:
    """Orders, fills and adjustments for one window, one order per trade."""

    def __init__(self) -> None:
        self.orders: list[OrderRow] = []
        self.fills: list[OrderedFill] = []
        self.adjustments: list[AdjustmentRow] = []

    def trade(
        self,
        side: str,
        quantity: float,
        price: float,
        day: date,
        *,
        security_id: str = "SEC_X",
        position_side: str = LONG,
    ) -> str:
        symbol = SYMBOLS[security_id]
        coid = f"o{len(self.orders) + 1}"
        opening = side == ("sell" if position_side == SHORT else "buy")
        order = OrderRow(
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
        self.orders.append(order)
        self.fills.append(OrderedFill(row, side, security_id, symbol, 1, 1))
        return coid

    def adjust(
        self,
        kind: str,
        session: date,
        security_id: str,
        *,
        quantity: float | None = None,
        cash: float | None = None,
        explanation: Mapping[str, Any] | None = None,
    ) -> AdjustmentRow:
        known = session_close(session) + timedelta(minutes=30)
        row = AdjustmentRow(
            adjustment_id=next(_adjustment_ids),
            window_id=1,
            run_id=1,
            session=session,
            kind=kind,
            security_id=security_id,
            quantity=quantity,
            cash=cash,
            explanation_json=None
            if explanation is None
            else json.dumps(dict(explanation), sort_keys=True),
            known_at=known,
            ingested_at=known,
        )
        self.adjustments.append(row)
        return row

    def run(
        self,
        through: date,
        actions: pl.DataFrame | None = None,
        adjustments: Sequence[AdjustmentRow] | None = None,
    ) -> tuple[list[lots.Lot], list[lots.Disposal], list[lots.WashSaleFlag]]:
        return rebuild(
            self.fills,
            self.orders,
            {},
            ACCOUNT,
            adjustments=self.adjustments if adjustments is None else adjustments,
            actions=actions if actions is not None else _splits(),
            through=through,
            symbols=SYMBOLS,
        )


SYMBOLS = {
    "SEC_X": "XYZ",
    "SEC_P": "PAR",
    "SEC_C": "KID",
    "SEC_T": "TGT",
    "SEC_A": "ACQ",
}


def _splits(*rows: tuple[str, date, float]) -> pl.DataFrame:
    """A frame shaped like `live_actions_as_of`, holding only split rows."""
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


def _basis_closed(lot_id: int, disposals: Sequence[lots.Disposal]) -> float:
    """The basis a lot's disposals closed: sum of (proceeds - realised P&L)."""
    return sum(d.proceeds - d.realised_pnl for d in disposals if d.lot_id == lot_id)


def _of(security_id: str, lot_list: Sequence[lots.Lot]) -> list[lots.Lot]:
    return [lot for lot in lot_list if lot.security_id == security_id]


# --- splits -----------------------------------------------------------------------


def test_a_forward_split_scales_open_lots_and_keeps_basis_and_date() -> None:
    """2:1 split on SEC_X, ex 03-07. Hand values:

      03-03 buy 10 @ 100   lot 1: 10 shares, basis 1000   -> after the split 20, basis 1000
      03-07 buy  4 @  50   lot 2: on the ex-date, already post-split: 4, basis 200
      03-12 sell 20 @ 55   closes lot 1 whole: proceeds 1100, pnl 1100 - 1000 = +100
    Without the split the same trip is 10 @ 100 sold 10 @ 110: pnl +100 too.
    """
    book = Book()
    book.trade("buy", 10, 100.0, D0303)
    book.trade("buy", 4, 50.0, D0307)
    sale = book.trade("sell", 20, 55.0, D0312)

    lot_list, disposals, flags = book.run(D0312, _splits(("SEC_X", D0307, 2.0)))

    assert [(lot.quantity, lot.cost_basis) for lot in lot_list] == [(20.0, 1000.0), (4.0, 200.0)]
    assert [lot.trade_at for lot in lot_list] == [_at(D0303), _at(D0307)]
    assert [lot.trade_date_local for lot in lot_list] == [D0303, D0307]
    assert [
        (d.lot_id, d.quantity, d.proceeds, d.realised_pnl, d.client_order_id) for d in disposals
    ] == [(1, 20.0, 1100.0, 100.0, sale)]
    assert flags == []

    # Realised P&L is the split-free round trip's.
    plain = Book()
    plain.trade("buy", 10, 100.0, D0303)
    plain.trade("sell", 10, 110.0, D0312)
    _, plain_disposals, _ = plain.run(D0312)
    assert sum(d.realised_pnl for d in disposals) == sum(d.realised_pnl for d in plain_disposals)


def test_a_split_after_through_is_not_applied() -> None:
    """The same split, rebuilt as of 03-06 (ex-date 03-07 is later): lot 1
    stays at 10 shares."""
    book = Book()
    book.trade("buy", 10, 100.0, D0303)

    lot_list, disposals, _ = book.run(D0306, _splits(("SEC_X", D0307, 2.0)))

    assert [(lot.quantity, lot.cost_basis) for lot in lot_list] == [(10.0, 1000.0)]
    assert disposals == []


def test_a_lot_partly_sold_before_a_split_keeps_its_basis_whole() -> None:
    """Disposals keep the share units of their own sale. Hand values:

      03-03 buy  10 @ 100   lot 1: basis 1000
      03-04 sell  4 @ 110   4 shares, proceeds 440, basis 400, pnl +40
      03-07 2:1 split       the open 6 become 12, open basis 600
      03-12 sell 12 @  60   12 shares, proceeds 720, basis 600, pnl +120
    The lot's disposals close 400 + 600 = 1000, its whole basis.
    """
    book = Book()
    book.trade("buy", 10, 100.0, D0303)
    book.trade("sell", 4, 110.0, D0304)
    book.trade("sell", 12, 60.0, D0312)

    lot_list, disposals, _ = book.run(D0312, _splits(("SEC_X", D0307, 2.0)))

    [lot] = lot_list
    assert (lot.cost_basis, lot.trade_date_local) == (1000.0, D0303)
    assert [(d.lot_id, d.quantity, d.proceeds, d.realised_pnl) for d in disposals] == [
        (1, 4.0, 440.0, 40.0),
        (1, 12.0, 720.0, 120.0),
    ]
    assert _basis_closed(1, disposals) == pytest.approx(1000.0)


def test_a_reverse_split_with_cash_in_lieu_disposes_of_the_fraction() -> None:
    """1-for-3 reverse split on SEC_X, ex 03-07, with cash in lieu of the
    fractional share. Hand values:

      03-03 buy 7 @ 10        lot 1: 7 shares, basis 70
      03-07 1:3 split         7/3 = 2.333... shares, basis 70 (30 a share)
      03-07 cash_in_lieu      -1/3 share for 10.50: basis 70 x (1/3)/(7/3) = 10,
                              disposal of 1/3 share, proceeds 10.50, pnl +0.50
      03-12 sell 2 @ 33       proceeds 66, basis 60, pnl +6
    The lot's disposals close 10 + 60 = 70.
    """
    book = Book()
    book.trade("buy", 7, 10.0, D0303)
    book.adjust("cash_in_lieu", D0307, "SEC_X", quantity=-1 / 3, cash=10.5)
    sale = book.trade("sell", 2, 33.0, D0312)

    lot_list, disposals, flags = book.run(D0312, _splits(("SEC_X", D0307, 1 / 3)))

    [lot] = lot_list
    assert lot.quantity == pytest.approx(7 / 3)
    assert (lot.cost_basis, lot.trade_date_local) == (70.0, D0303)
    cil, sold = disposals
    assert (cil.lot_id, cil.trade_date_local, cil.tax_year) == (1, D0307, 2025)
    assert cil.quantity == pytest.approx(1 / 3)
    assert cil.proceeds == pytest.approx(10.5)
    assert cil.realised_pnl == pytest.approx(0.5)
    assert cil.fill_id is None
    assert cil.client_order_id not in {order.client_order_id for order in book.orders}
    assert cil.trade_at.tzinfo is not None
    assert (sold.lot_id, sold.client_order_id) == (1, sale)
    assert sold.quantity == pytest.approx(2.0)
    assert sold.proceeds == pytest.approx(66.0)
    assert sold.realised_pnl == pytest.approx(6.0)
    assert _basis_closed(1, disposals) == pytest.approx(70.0)
    assert flags == []


def test_the_lot_ledger_and_the_positions_ledger_agree_on_a_reverse_split() -> None:
    """#2184: 15 shares through a 1-for-10 reverse split are 1.5; the broker holds
    1 whole share and pays cash in lieu for the 0.5. The lot ledger (the 1.5-share
    lot less the 0.5 disposed) and the positions ledger both end at 1 share."""
    book = Book()
    book.trade("buy", 15, 10.0, D0303)
    book.adjust("cash_in_lieu", D0307, "SEC_X", quantity=-0.5, cash=50.0)
    actions = _splits(("SEC_X", D0307, 0.1))

    lot_list, disposals, _ = book.run(D0312, actions)
    positions = from_journal(
        book.fills,
        book.orders,
        book.adjustments,
        actions,
        None,
        100_000.0,
        D0312,
        window_id=1,
        quantity_tolerance=1e-6,
    ).positions

    [lot] = lot_list
    [cil] = disposals
    assert lot.quantity - cil.quantity == pytest.approx(1.0)  # what stays open
    assert float(positions["SEC_X"]) == pytest.approx(1.0)
    assert cil.quantity == pytest.approx(0.5)
    assert cil.proceeds == pytest.approx(50.0)
    assert cil.realised_pnl == pytest.approx(0.0)  # basis 150 x (0.5 / 1.5) = 50


def test_cash_in_lieu_leaves_whole_shares_so_nothing_is_stranded() -> None:
    """The positions ledger's rule (#1627): cash in lieu rounds the name's exact
    holding down to whole shares, never subtracting the float remainder (which
    leaves a sub-ulp share). After the reverse split, the cash in lieu and the
    2-share sale above, no long lot is open, so a short sale of SEC_X on 03-13
    opens a short lot instead of raising "never long and short at once"."""
    book = Book()
    book.trade("buy", 7, 10.0, D0303)
    book.adjust("cash_in_lieu", D0307, "SEC_X", quantity=-1 / 3, cash=10.5)
    book.trade("sell", 2, 33.0, D0312)
    book.trade("sell", 1, 30.0, D0313, position_side=SHORT)

    lot_list, _, _ = book.run(D0313, _splits(("SEC_X", D0307, 1 / 3)))

    assert [(lot.position_side, lot.quantity) for lot in lot_list] == [
        (LONG, pytest.approx(7 / 3)),
        (SHORT, 1.0),
    ]


def test_the_wash_sale_window_and_holding_dates_carry_across_a_split() -> None:
    """A loss after a 2:1 split is matched to a pre-split purchase in post-split
    shares. Hand values:

      03-03 buy 10 @ 100   lot 1 -> 20 shares after the split, basis 1000
      03-05 buy 10 @ 100   lot 2 -> 20 shares after the split, basis 1000
      03-07 2:1 split
      03-12 sell 20 @ 40   closes lot 1 whole: proceeds 800, pnl -200
            lot 2 (bought 7 days before the loss) replaces all 20: disallowed 200
    """
    book = Book()
    book.trade("buy", 10, 100.0, D0303)
    book.trade("buy", 10, 100.0, D0305)
    book.trade("sell", 20, 40.0, D0312)

    lot_list, disposals, flags = book.run(D0312, _splits(("SEC_X", D0307, 2.0)))

    assert [(lot.quantity, lot.trade_date_local) for lot in lot_list] == [
        (20.0, D0303),
        (20.0, D0305),
    ]
    assert [(d.lot_id, d.quantity, d.proceeds, d.realised_pnl) for d in disposals] == [
        (1, 20.0, 800.0, -200.0)
    ]
    assert [
        (f.disposal_id, f.replacement_lot_id, f.matched_quantity, f.disallowed_amount)
        for f in flags
    ] == [(1, 2, 20.0, 200.0)]


# --- the split's no-look-ahead through the store ---------------------------------


@pytest.fixture
def store() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def _store_split(
    conn: duckdb.DuckDBPyConnection, security_id: str, ex_date: date, ratio: float, known: datetime
) -> None:
    insert_row(
        conn,
        "corporate_actions",
        {
            "security_id": security_id,
            "action_type": "split",
            "ex_date": ex_date,
            "ratio_or_amount": ratio,
            "announced_at": known,
            "known_at": known,
            "ingested_at": known + timedelta(minutes=5),
            "source": "alpaca",
            "provenance": "action",
        },
    )


def test_a_split_known_after_close_of_s_minus_1_is_not_applied(
    store: duckdb.DuckDBPyConnection,
) -> None:
    """Rebuild as of S = 03-10, so the cut is close(03-07) = 21:00 UTC. SEC_X's
    2:1 split, ex 03-07, is known only at 22:00 UTC on 03-07: not applied (10
    shares). The same journal read after it is known applies it (20)."""
    _store_split(store, "SEC_X", D0307, 2.0, datetime(2025, 3, 7, 22, tzinfo=UTC))
    book = Book()
    book.trade("buy", 10, 100.0, D0303)

    before = book.run(D0310, live_actions_as_of(store, session_close(D0307)))
    after = book.run(D0310, live_actions_as_of(store, datetime(2025, 3, 7, 23, tzinfo=UTC)))

    assert [(lot.quantity, lot.cost_basis) for lot in before[0]] == [(10.0, 1000.0)]
    assert [(lot.quantity, lot.cost_basis) for lot in after[0]] == [(20.0, 1000.0)]


# --- spin-offs --------------------------------------------------------------------

_RECONCILE_EXPLANATION = {"explanation": "spin-off child of SEC_P", "reconciliation_id": 7}


def test_a_spinoff_child_named_without_terms_is_a_zero_basis_lot() -> None:
    """Today's `explanation_json` (reconcile_run's, no basis terms): the child
    lot carries zero basis and the parent keeps its whole basis (req 13: the
    gain on the child's sale is overstated, never understated). Hand values:

      03-03 buy SEC_P 10 @ 100          parent lot: basis 1000
      03-07 spinoff_receipt SEC_C 5     child lot: 5 shares, basis 0, dated 03-07
      03-12 sell SEC_C 5 @ 8            proceeds 40, pnl +40 (= proceeds)
    """
    book = Book()
    book.trade("buy", 10, 100.0, D0303, security_id="SEC_P")
    book.adjust("spinoff_receipt", D0307, "SEC_C", quantity=5.0, explanation=_RECONCILE_EXPLANATION)
    sale = book.trade("sell", 5, 8.0, D0312, security_id="SEC_C")

    lot_list, disposals, flags = book.run(D0312)

    [parent] = _of("SEC_P", lot_list)
    [child] = _of("SEC_C", lot_list)
    assert (parent.quantity, parent.cost_basis) == (10.0, 1000.0)
    assert (child.symbol, child.quantity, child.cost_basis) == ("KID", 5.0, 0.0)
    assert (child.trade_date_local, child.fill_id, child.position_side) == (D0307, None, LONG)
    assert (child.account_id, child.book_id) == (ACCOUNT.account_id, "main")
    assert child.client_order_id not in {order.client_order_id for order in book.orders}
    assert child.trade_at.tzinfo is not None
    assert [
        (d.lot_id, d.quantity, d.proceeds, d.realised_pnl, d.client_order_id) for d in disposals
    ] == [(child.lot_id, 5.0, 40.0, 40.0, sale)]
    assert flags == []


def test_a_spinoff_receipt_with_no_explanation_is_a_zero_basis_lot() -> None:
    """`paper start` journals a carried parent's spin-off with no
    `explanation_json` at all (`window.py`): the child lot is zero-basis, as
    with today's reconcile text, and nothing raises."""
    book = Book()
    book.trade("buy", 10, 100.0, D0303, security_id="SEC_P")
    book.adjust("spinoff_receipt", D0307, "SEC_C", quantity=5.0)
    book.trade("sell", 5, 8.0, D0312, security_id="SEC_C")

    lot_list, disposals, _ = book.run(D0312)

    [parent] = _of("SEC_P", lot_list)
    [child] = _of("SEC_C", lot_list)
    assert (parent.cost_basis, child.quantity, child.cost_basis) == (1000.0, 5.0, 0.0)
    assert child.trade_date_local == D0307
    assert [(d.lot_id, d.proceeds, d.realised_pnl) for d in disposals] == [
        (child.lot_id, 40.0, 40.0)
    ]


def test_a_spinoff_receipt_after_through_opens_no_lot() -> None:
    book = Book()
    book.trade("buy", 10, 100.0, D0303, security_id="SEC_P")
    book.adjust("spinoff_receipt", D0307, "SEC_C", quantity=5.0, explanation=_RECONCILE_EXPLANATION)

    lot_list, _, _ = book.run(D0306)

    assert _of("SEC_C", lot_list) == []


def test_a_spinoff_with_a_basis_allocation_moves_that_share_of_the_parents_basis() -> None:
    """`explanation_json` names the parent and the fraction of its basis that
    moves to the child (0.2). Hand values:

      03-03 buy SEC_P 10 @ 100          parent lot: basis 1000
      03-07 spinoff_receipt SEC_C 5     parent basis 1000 x 0.8 = 800; child basis 200
      03-12 sell SEC_C 5 @ 8            proceeds 40, pnl 40 - 200 = -160
      03-13 sell SEC_P 10 @ 90          proceeds 900, pnl 900 - 800 = +100
    Total basis 800 + 200 = 1000, the cash paid.
    """
    book = Book()
    book.trade("buy", 10, 100.0, D0303, security_id="SEC_P")
    book.adjust(
        "spinoff_receipt",
        D0307,
        "SEC_C",
        quantity=5.0,
        explanation={
            **_RECONCILE_EXPLANATION,
            "parent_security_id": "SEC_P",
            "basis_allocation": 0.2,
        },
    )
    book.trade("sell", 5, 8.0, D0312, security_id="SEC_C")
    book.trade("sell", 10, 90.0, D0313, security_id="SEC_P")

    lot_list, disposals, _ = book.run(D0313)

    [parent] = _of("SEC_P", lot_list)
    [child] = _of("SEC_C", lot_list)
    assert parent.cost_basis == pytest.approx(800.0)
    assert parent.trade_date_local == D0303
    # Amendment #1976 item 3: the child carries the parent lot's acquisition date.
    assert (child.quantity, child.trade_date_local) == (5.0, D0303)
    assert child.cost_basis == pytest.approx(200.0)
    by_lot = {d.lot_id: d for d in disposals}
    assert by_lot[child.lot_id].realised_pnl == pytest.approx(-160.0)
    assert by_lot[parent.lot_id].realised_pnl == pytest.approx(100.0)
    assert parent.cost_basis + child.cost_basis == pytest.approx(1000.0)


# --- stock mergers (paper spec amendment #1762 item 5, T249b) --------------------

#: Item 5: "Two rows are journaled, both naming the merger id". The key name
#: `merger_id` is this lock's assumption (the spec names no key).
_MERGER = {"merger_id": "alpaca-merger-1"}


def _merge(book: Book, session: date, removed: float, received: float, cash: float = 0.0) -> None:
    """Item 5's pair on one session: a `corporate_action_cash` row removing the
    acquiree (signed delta, with the cash difference) and a `merger_receipt`
    row adding the acquirer's shares, both naming the merger id."""
    book.adjust(
        "corporate_action_cash",
        session,
        "SEC_T",
        quantity=-removed,
        cash=cash,
        explanation=_MERGER,
    )
    book.adjust("merger_receipt", session, "SEC_A", quantity=received, explanation=_MERGER)


def test_a_stock_merger_converts_target_lots_at_the_ratio() -> None:
    """SEC_T merges into SEC_A at 0.5 acquirer shares per target share, stock
    only, whole shares, no cash (item 5's rows; `merger_receipt` joins the
    `adjustments` kinds in T249b). Hand values:

      03-03 buy SEC_T 10 @ 30      lot: basis 300
      03-07 corporate_action_cash -10 SEC_T, cash 0 + merger_receipt +5 SEC_A
                                   -> an SEC_A lot of 5, basis 300, still dated 03-03
                                      no disposal (a tax-free exchange)
      03-12 sell SEC_A 5 @ 70      proceeds 350, pnl 350 - 300 = +50
    """
    book = Book()
    book.trade("buy", 10, 30.0, D0303, security_id="SEC_T")
    _merge(book, D0307, 10.0, 5.0)
    sale = book.trade("sell", 5, 70.0, D0312, security_id="SEC_A")

    lot_list, disposals, flags = book.run(D0312)

    [acquired] = _of("SEC_A", lot_list)
    assert (acquired.symbol, acquired.quantity, acquired.cost_basis) == ("ACQ", 5.0, 300.0)
    assert (acquired.trade_at, acquired.trade_date_local) == (_at(D0303), D0303)
    assert [
        (d.lot_id, d.quantity, d.proceeds, d.realised_pnl, d.trade_date_local, d.client_order_id)
        for d in disposals
    ] == [(acquired.lot_id, 5.0, 350.0, 50.0, D0312, sale)]
    assert flags == []


def test_a_cash_merger_removal_without_a_receipt_is_still_a_disposal() -> None:
    """Item 2's cash merger: one `corporate_action_cash` row naming the merger
    id and no `merger_receipt`. It closes the lot as a sale at its cash:
    10 @ 30 (basis 300) removed for 320, pnl +20."""
    book = Book()
    book.trade("buy", 10, 30.0, D0303, security_id="SEC_T")
    book.adjust(
        "corporate_action_cash", D0307, "SEC_T", quantity=-10.0, cash=320.0, explanation=_MERGER
    )

    lot_list, disposals, _ = book.run(D0312)

    assert _of("SEC_A", lot_list) == []
    assert [(d.quantity, d.proceeds, d.realised_pnl, d.trade_date_local) for d in disposals] == [
        (10.0, 320.0, 20.0, D0307)
    ]


def test_after_a_stock_merger_the_target_is_no_longer_held() -> None:
    book = Book()
    book.trade("buy", 10, 30.0, D0303, security_id="SEC_T")
    _merge(book, D0307, 10.0, 5.0)
    book.trade("sell", 1, 30.0, D0312, security_id="SEC_T")

    with pytest.raises(LotLedgerError, match="more than"):
        book.run(D0312)


# --- carried residue and cash removal ---------------------------------------------


def test_a_carried_residue_is_a_zero_basis_lot_and_never_a_replacement() -> None:
    """Hand values:

      03-03 carried_residue 3 (window start)   lot 1: 3 shares, basis 0, dated 03-03
      03-04 buy 10 @ 50                        lot 2: basis 500
      03-05 sell 3 @ 45                        FIFO closes lot 1: proceeds 135, pnl +135
      03-07 sell 10 @ 40                       closes lot 2: proceeds 400, pnl -100
    Lot 1 (3 shares inside the window, not sold by the 03-07 sale) would
    replace 3 shares of that loss under the literal rule, but nothing was
    bought: no flag.
    """
    book = Book()
    book.adjust("carried_residue", D0303, "SEC_X", quantity=3.0)
    book.trade("buy", 10, 50.0, D0304)
    book.trade("sell", 3, 45.0, D0305)
    book.trade("sell", 10, 40.0, D0307)

    lot_list, disposals, flags = book.run(D0307)

    assert [
        (lot.quantity, lot.cost_basis, lot.trade_date_local, lot.fill_id) for lot in lot_list
    ] == [(3.0, 0.0, D0303, None), (10.0, 500.0, D0304, book.fills[0].fill.fill_id)]
    assert [(d.lot_id, d.quantity, d.proceeds, d.realised_pnl) for d in disposals] == [
        (1, 3.0, 135.0, 135.0),
        (2, 10.0, 400.0, -100.0),
    ]
    assert flags == []


def test_a_carried_residue_is_dated_on_its_session_and_split_adjusted_from_it() -> None:
    """`paper start` journals a residue at the previous window's stop session
    (`window.py`), not the new window's start, and the positions ledger
    split-adjusts it from that session. Hand values:

      03-03 carried_residue 3 (the previous stop session)   lot: 3 shares, basis 0
      03-05 2:1 split                                        the lot is 6 shares
      03-10 sell 6 @ 20                                      proceeds 120, pnl +120
    """
    book = Book()
    book.adjust("carried_residue", D0303, "SEC_X", quantity=3.0)
    book.trade("sell", 6, 20.0, D0310)

    lot_list, disposals, _ = book.run(D0310, _splits(("SEC_X", D0305, 2.0)))

    assert [(lot.quantity, lot.cost_basis, lot.trade_date_local) for lot in lot_list] == [
        (6.0, 0.0, D0303)
    ]
    assert [(d.quantity, d.proceeds, d.realised_pnl) for d in disposals] == [(6.0, 120.0, 120.0)]


def test_corporate_action_cash_closes_the_open_lots_fifo_at_the_journaled_cash() -> None:
    """An ended listing removes 10 shares for 420. Hand values:

      03-03 buy 6 @ 50   lot 1: basis 300
      03-04 buy 4 @ 55   lot 2: basis 220
      03-10 corporate_action_cash -10, +420: one sale, FIFO
            lot 1: 6 shares, proceeds 420 x 6/10 = 252, pnl -48
            lot 2: 4 shares, proceeds 420 x 4/10 = 168, pnl -52
    Both lots are closed by the same removal, so neither replaces the other's
    loss: no flag.
    """
    book = Book()
    book.trade("buy", 6, 50.0, D0303)
    book.trade("buy", 4, 55.0, D0304)
    book.adjust("corporate_action_cash", D0310, "SEC_X", quantity=-10.0, cash=420.0)

    _, disposals, flags = book.run(D0310)

    assert [(d.lot_id, d.quantity, d.trade_date_local, d.tax_year) for d in disposals] == [
        (1, 6.0, D0310, 2025),
        (2, 4.0, D0310, 2025),
    ]
    assert [d.proceeds for d in disposals] == pytest.approx([252.0, 168.0])
    assert [d.realised_pnl for d in disposals] == pytest.approx([-48.0, -52.0])
    assert all(d.fill_id is None for d in disposals)
    orders = {order.client_order_id for order in book.orders}
    assert all(d.client_order_id not in orders for d in disposals)
    assert flags == []


# --- short lots keep halting -------------------------------------------------------


def _short_open(book: Book, security_id: str = "SEC_X") -> None:
    book.trade("sell", 10, 50.0, D0303, security_id=security_id, position_side=SHORT)


def test_a_split_on_an_open_short_lot_raises() -> None:
    book = Book()
    _short_open(book)

    with pytest.raises(LotLedgerError, match="short lot"):
        book.run(D0310, _splits(("SEC_X", D0307, 2.0)))


# Signed deltas as a short would show them; any such row on an open short raises.
@pytest.mark.parametrize(
    ("kind", "quantity", "cash"),
    [
        ("corporate_action_cash", 10.0, -500.0),
        ("cash_in_lieu", 0.5, -25.0),
        ("spinoff_receipt", -5.0, None),
        ("carried_residue", -3.0, None),
    ],
)
def test_an_adjustment_on_an_open_short_lot_raises(
    kind: str, quantity: float, cash: float | None
) -> None:
    book = Book()
    _short_open(book)
    book.adjust(kind, D0307, "SEC_X", quantity=quantity, cash=cash)

    with pytest.raises(LotLedgerError, match="short lot"):
        book.run(D0310)


@pytest.mark.parametrize("kind", ["spinoff_receipt", "carried_residue"])
def test_a_negative_receipt_or_residue_raises_with_no_short_held(kind: str) -> None:
    """A negative `spinoff_receipt` or `carried_residue` is a short's receipt
    or a carried short: it raises even when no short lot is open yet."""
    book = Book()
    book.adjust(kind, D0307, "SEC_C", quantity=-5.0)

    with pytest.raises(LotLedgerError, match="short lot"):
        book.run(D0310)


# --- spin-off child carries the parent's holding period (T255, #1976 item 3) -------

_P1 = date(2022, 1, 3)
_P2 = date(2023, 6, 1)
_EX = date(2023, 9, 15)  # the spin-off's ex-date; the entitlement is fixed at close(09-14)
_GAP = date(2023, 9, 18)  # a session between the ex-date and the receipt
_RECEIPT = date(2023, 9, 20)
_SALE = date(2023, 9, 22)


def _terms(allocation: float | None, ex_date: date | None = _EX) -> dict[str, Any]:
    terms: dict[str, Any] = {**_RECONCILE_EXPLANATION, "parent_security_id": "SEC_P"}
    if allocation is not None:
        terms["basis_allocation"] = allocation
    if ex_date is not None:
        terms["ex_date"] = ex_date.isoformat()
    return terms


def _two_lot_parent() -> Book:
    book = Book()
    book.trade("buy", 100, 10.0, _P1, security_id="SEC_P")
    book.trade("buy", 100, 14.0, _P2, security_id="SEC_P")
    return book


def test_the_child_carries_a_lot_per_parent_lot_with_the_allocation() -> None:
    """The amendment's two-lot hand case, 1:1 spin-off, allocation 0.2:

      2022-01-03 buy PAR 100 @ 10   parent lot 1: basis 1000
      2023-06-01 buy PAR 100 @ 14   parent lot 2: basis 1400
      receipt 100 + 100 KID         child lot 1: 100 sh, basis 1000 x 0.2 = 200, 2022-01-03
                                    child lot 2: 100 sh, basis 1400 x 0.2 = 280, 2023-06-01
                                    parent lots 800 and 1120; 800 + 1120 + 200 + 280 = 2400
    A sale of 100 KID closes child lot 1 (FIFO by `trade_at`), held 628 days: long-term.
    """
    book = _two_lot_parent()
    book.adjust("spinoff_receipt", _RECEIPT, "SEC_C", quantity=200.0, explanation=_terms(0.2))
    book.trade("sell", 100, 5.0, _SALE, security_id="SEC_C")

    lot_list, disposals, flags = book.run(_SALE)

    parents, kids = _of("SEC_P", lot_list), _of("SEC_C", lot_list)
    assert [(p.cost_basis, p.trade_date_local) for p in parents] == [(800.0, _P1), (1120.0, _P2)]
    assert [(k.quantity, k.cost_basis, k.trade_date_local) for k in kids] == [
        (100.0, pytest.approx(200.0), _P1),
        (100.0, pytest.approx(280.0), _P2),
    ]
    assert kids[0].trade_at == _at(_P1)
    assert sum(lot.cost_basis for lot in lot_list) == pytest.approx(2400.0)
    [sale] = disposals
    assert sale.lot_id == kids[0].lot_id
    assert (sale.trade_date_local - kids[0].trade_date_local).days > 365  # long-term
    assert (sale.trade_date_local - kids[1].trade_date_local).days <= 365  # the second: short
    assert flags == []


def test_the_child_without_an_allocation_has_the_same_lots_at_zero_basis() -> None:
    book = _two_lot_parent()
    book.adjust("spinoff_receipt", _RECEIPT, "SEC_C", quantity=200.0, explanation=_terms(None))

    lot_list, _, _ = book.run(_RECEIPT)

    assert [(k.quantity, k.cost_basis, k.trade_date_local) for k in _of("SEC_C", lot_list)] == [
        (100.0, 0.0, _P1),
        (100.0, 0.0, _P2),
    ]
    assert [p.cost_basis for p in _of("SEC_P", lot_list)] == [1000.0, 1400.0]


def test_a_parent_bought_after_the_ex_date_gets_no_child_lot() -> None:
    """The lot set is fixed at close(09-14): 50 PAR bought on 09-18, before the
    receipt, is open when the receipt is journaled but earns no child share."""
    book = Book()
    book.trade("buy", 100, 10.0, _P1, security_id="SEC_P")
    book.trade("buy", 50, 12.0, _GAP, security_id="SEC_P")
    book.adjust("spinoff_receipt", _RECEIPT, "SEC_C", quantity=100.0, explanation=_terms(0.2))

    lot_list, _, _ = book.run(_RECEIPT)

    [kid] = _of("SEC_C", lot_list)
    assert (kid.quantity, kid.trade_date_local) == (100.0, _P1)
    assert kid.cost_basis == pytest.approx(200.0)
    assert [p.cost_basis for p in _of("SEC_P", lot_list)] == [pytest.approx(800.0), 600.0]


def test_a_parent_lot_half_sold_in_the_gap_keeps_its_full_child_quantity() -> None:
    """100 PAR @ 10, 50 sold on 09-18: the child lot is still 100 shares and
    only the open half's basis moves: 500 x 0.2 = 100; the parent keeps 400."""
    book = Book()
    book.trade("buy", 100, 10.0, _P1, security_id="SEC_P")
    book.trade("sell", 50, 11.0, _GAP, security_id="SEC_P")
    book.adjust("spinoff_receipt", _RECEIPT, "SEC_C", quantity=100.0, explanation=_terms(0.2))

    lot_list, _, _ = book.run(_RECEIPT)

    [kid] = _of("SEC_C", lot_list)
    assert (kid.quantity, kid.trade_date_local) == (100.0, _P1)
    assert kid.cost_basis == pytest.approx(100.0)
    [open_parent] = [
        lot
        for lot in lots.open_lots(
            book.fills,
            book.orders,
            {},
            ACCOUNT,
            adjustments=book.adjustments,
            actions=_splits(),
            through=_RECEIPT,
            symbols=SYMBOLS,
        )
        if lot.security_id == "SEC_P"
    ]
    assert (open_parent.quantity, open_parent.basis) == (50.0, pytest.approx(400.0))


def test_a_parent_lot_fully_sold_in_the_gap_gives_a_zero_basis_child_lot() -> None:
    book = Book()
    book.trade("buy", 100, 10.0, _P1, security_id="SEC_P")
    book.trade("sell", 100, 11.0, _GAP, security_id="SEC_P")
    book.adjust("spinoff_receipt", _RECEIPT, "SEC_C", quantity=100.0, explanation=_terms(0.2))

    lot_list, _, _ = book.run(_RECEIPT)

    [kid] = _of("SEC_C", lot_list)
    assert (kid.quantity, kid.cost_basis, kid.trade_date_local) == (100.0, 0.0, _P1)


def test_a_parent_with_no_lot_open_at_the_close_opens_one_zero_basis_child_lot() -> None:
    """The parent is bought only after the ex-date: nothing is entitled, one
    child lot opens on the receipt's session with basis 0 and nothing raises."""
    book = Book()
    book.trade("buy", 50, 12.0, _GAP, security_id="SEC_P")
    book.adjust("spinoff_receipt", _RECEIPT, "SEC_C", quantity=25.0, explanation=_terms(0.2))

    lot_list, _, _ = book.run(_RECEIPT)

    [kid] = _of("SEC_C", lot_list)
    assert (kid.quantity, kid.cost_basis, kid.trade_date_local) == (25.0, 0.0, _RECEIPT)
    assert _of("SEC_P", lot_list)[0].cost_basis == 600.0


def test_a_carried_child_lot_sorts_with_the_parents_era_in_fifo() -> None:
    """A child lot carried to 2022-01-03 is sold before a KID lot the book
    bought in 2022-06, though the carried one is created later."""
    book = Book()
    book.trade("buy", 100, 10.0, _P1, security_id="SEC_P")
    bought = date(2022, 6, 1)
    book.trade("buy", 10, 3.0, bought, security_id="SEC_C")
    book.adjust("spinoff_receipt", _RECEIPT, "SEC_C", quantity=100.0, explanation=_terms(0.2))
    book.trade("sell", 100, 5.0, _SALE, security_id="SEC_C")

    lot_list, disposals, _ = book.run(_SALE)

    carried = next(k for k in _of("SEC_C", lot_list) if k.trade_date_local == _P1)
    own = next(k for k in _of("SEC_C", lot_list) if k.trade_date_local == bought)
    assert [(d.lot_id, d.quantity) for d in disposals] == [(carried.lot_id, 100.0)]
    assert own.quantity == 10.0


def test_the_carried_child_lot_is_absent_before_the_receipts_session() -> None:
    """`trade_at` is the parent's 2022-01-03 but the lot does not exist before
    the receipt's session. Every reader of `lots.trade_at` sits behind the
    rebuild: `_wash_sale_flags` (never a replacement), `_close_fifo` (the
    held list), `outcomes._append_ledger` and `lots_reconcile` (both read the
    rebuilt rows only) and `cli` (prints them)."""
    book = _two_lot_parent()
    book.adjust("spinoff_receipt", _RECEIPT, "SEC_C", quantity=200.0, explanation=_terms(0.2))

    before, _, _ = book.run(_GAP)
    at, _, _ = book.run(_RECEIPT)

    assert _of("SEC_C", before) == []
    assert [p.cost_basis for p in _of("SEC_P", before)] == [1000.0, 1400.0]
    assert len(_of("SEC_C", at)) == 2


def test_the_carried_child_lot_is_never_a_wash_sale_replacement() -> None:
    """A parent loss sale inside the window of the carried lot's date: the
    child lot, though dated with the parent's era, is no replacement."""
    book = Book()
    book.trade("buy", 100, 10.0, _P1, security_id="SEC_P")
    book.adjust("spinoff_receipt", _RECEIPT, "SEC_C", quantity=100.0, explanation=_terms(0.2))
    book.trade("sell", 100, 1.0, _SALE, security_id="SEC_P")
    book.trade("sell", 100, 1.0, _SALE, security_id="SEC_C")

    _, _, flags = book.run(_SALE)

    assert flags == []


def test_a_spinoff_whose_named_parent_is_held_short_raises() -> None:
    book = Book()
    _short_open(book, "SEC_P")
    book.adjust(
        "spinoff_receipt",
        D0307,
        "SEC_C",
        quantity=5.0,
        explanation={
            **_RECONCILE_EXPLANATION,
            "parent_security_id": "SEC_P",
            "basis_allocation": 0.2,
        },
    )

    with pytest.raises(LotLedgerError, match="short lot"):
        book.run(D0310)


def test_a_stock_merger_of_an_open_short_lot_raises() -> None:
    book = Book()
    _short_open(book, "SEC_T")
    # Item 6: a stock merger on a short halts at reconciliation, so these rows
    # should never be journaled; if they were (the short's signed deltas: +10
    # target out, -5 acquirer in), the lot ledger still halts.
    _merge(book, D0307, -10.0, -5.0)

    with pytest.raises(LotLedgerError, match="short lot"):
        book.run(D0310)


def test_a_split_after_a_short_is_covered_does_not_raise() -> None:
    """Covered on 03-04, before the 03-07 ex-date: no open short lot, so the
    split touches only the long bought after it (4 @ 25 on 03-10, post-split)."""
    book = Book()
    _short_open(book)
    book.trade("buy", 10, 48.0, D0304, position_side=SHORT)
    book.trade("buy", 4, 25.0, D0310)

    lot_list, disposals, _ = book.run(D0310, _splits(("SEC_X", D0307, 2.0)))

    assert [(lot.position_side, lot.quantity, lot.cost_basis) for lot in lot_list] == [
        (SHORT, 10.0, -500.0),
        (LONG, 4.0, 100.0),
    ]
    assert [(d.position_side, d.realised_pnl) for d in disposals] == [(SHORT, 20.0)]


# --- determinism ------------------------------------------------------------------


def test_the_rebuild_is_deterministic_whatever_the_adjustments_order() -> None:
    """Every kind in one window: the same inputs give equal ledgers, and so
    does the adjustments list reversed."""
    book = Book()
    book.adjust("carried_residue", D0303, "SEC_X", quantity=3.0)
    book.trade("buy", 7, 10.0, D0304)
    book.adjust("cash_in_lieu", D0307, "SEC_X", quantity=-1 / 3, cash=10.5)
    book.trade("buy", 10, 100.0, D0303, security_id="SEC_P")
    book.adjust("spinoff_receipt", D0307, "SEC_C", quantity=5.0, explanation=_RECONCILE_EXPLANATION)
    book.trade("buy", 10, 30.0, D0304, security_id="SEC_T")
    _merge(book, D0310, 10.0, 5.0)
    book.trade("sell", 5, 8.0, D0312, security_id="SEC_C")
    book.trade("sell", 5, 70.0, D0313, security_id="SEC_A")
    actions = _splits(("SEC_X", D0307, 1 / 3))

    first = book.run(D0313, actions)
    second = book.run(D0313, actions)
    reversed_order = book.run(D0313, actions, adjustments=list(reversed(book.adjustments)))

    assert first == second == reversed_order
    assert len(first[1]) == 3  # the cash in lieu and the two sales


# --- #2151 owner decision: stock-merger cash and carried basis ---------------------


def test_stock_only_merger_carries_each_lot_basis_and_date() -> None:
    """Four target shares at 20 and six at 50 become 2 and 3 acquirer
    shares; basis 80 and 300, with each original acquisition date."""
    book = Book()
    book.trade("buy", 4, 20.0, D0303, security_id="SEC_T")
    book.trade("buy", 6, 50.0, D0304, security_id="SEC_T")
    terms = {
        **_MERGER,
        "merger_kind": "stock",
        "acquirer_security_id": "SEC_A",
        "share_ratio": 0.5,
        "acquirer_price": 60.0,
        "fractional_shares": 0.0,
    }
    book.adjust("corporate_action_cash", D0307, "SEC_T", quantity=-10, cash=0, explanation=terms)
    book.adjust("merger_receipt", D0307, "SEC_A", quantity=5, explanation=_MERGER)

    lot_list, disposals, _ = book.run(D0310)
    assert [(r.quantity, r.cost_basis, r.trade_date_local) for r in _of("SEC_A", lot_list)] == [
        (2.0, 80.0, D0303),
        (3.0, 300.0, D0304),
    ]
    assert disposals == []


def test_stock_and_cash_merger_sells_pro_rata_basis_for_removal_cash() -> None:
    """Cash 60 and four acquirer shares at 60 imply f=60/(60+240)=.2.
    Target lots 4 and 6 at 20 dispose basis 16 and 24 for proceeds 24
    and 36, carrying basis 64 and 96 into 1.6 and 2.4 shares."""
    book = Book()
    book.trade("buy", 4, 20.0, D0303, security_id="SEC_T")
    book.trade("buy", 6, 20.0, D0304, security_id="SEC_T")
    terms = {
        **_MERGER,
        "merger_kind": "stock_and_cash",
        "acquirer_security_id": "SEC_A",
        "share_ratio": 0.4,
        "acquirer_price": 60.0,
        "fractional_shares": 0.0,
    }
    book.adjust("corporate_action_cash", D0307, "SEC_T", quantity=-10, cash=60, explanation=terms)
    book.adjust("merger_receipt", D0307, "SEC_A", quantity=4, explanation=_MERGER)

    lot_list, disposals, _ = book.run(D0310)
    assert [(r.quantity, r.cost_basis) for r in _of("SEC_A", lot_list)] == [
        (1.6, 64.0),
        (2.4, 96.0),
    ]
    assert [(d.quantity, d.proceeds, d.realised_pnl) for d in disposals] == [
        (0.8, 24.0, 8.0),
        (1.2, 36.0, 12.0),
    ]


def test_stock_and_cash_merger_without_receipt_price_holds_back_lots() -> None:
    """Without a point-in-time acquirer price the sale fraction is unknown."""
    book = Book()
    book.trade("buy", 10, 20.0, D0303, security_id="SEC_T")
    terms = {**_MERGER, "merger_kind": "stock_and_cash", "fractional_shares": 0.0}
    book.adjust("corporate_action_cash", D0307, "SEC_T", quantity=-10, cash=60, explanation=terms)
    book.adjust("merger_receipt", D0307, "SEC_A", quantity=4, explanation=_MERGER)

    with pytest.raises(LotLedgerError, match="acquirer_price"):
        book.run(D0310)


def test_later_merger_cil_sells_fractional_acquirer_basis() -> None:
    """Four and six target shares with bases 200 and 300 receive 2 whole
    acquirer shares of 2.5 due. Later CIL sells .5/2.5 of each basis:
    40 and 60 basis for 20 and 30 proceeds, retaining 160 and 240."""
    book = Book()
    book.trade("buy", 4, 50.0, D0303, security_id="SEC_T")
    book.trade("buy", 6, 50.0, D0304, security_id="SEC_T")
    terms = {
        **_MERGER,
        "merger_kind": "stock",
        "acquirer_security_id": "SEC_A",
        "share_ratio": 0.25,
        "acquirer_price": 100.0,
        "fractional_shares": 0.5,
    }
    book.adjust("corporate_action_cash", D0307, "SEC_T", quantity=-10, cash=0, explanation=terms)
    book.adjust("merger_receipt", D0307, "SEC_A", quantity=2, explanation=_MERGER)
    book.adjust("corporate_action_cash", D0310, "SEC_T", cash=50, explanation=_MERGER)

    lot_list, disposals, _ = book.run(D0310)
    assert [(r.quantity, r.cost_basis) for r in _of("SEC_A", lot_list)] == [
        (0.8, 160.0),
        (1.2, 240.0),
    ]
    assert [(d.quantity, d.proceeds, d.realised_pnl) for d in disposals] == [
        (0.2, 20.0, -20.0),
        (0.3, 30.0, -30.0),
    ]


def test_zero_share_merger_disposes_all_lots_for_removal_and_later_cil_cash() -> None:
    """No acquirer shares: 4+6 target shares, basis 80+120, receive 100
    then 50 cash. Full disposal proceeds are 60+90, realised -20-30."""
    book = Book()
    book.trade("buy", 4, 20.0, D0303, security_id="SEC_T")
    book.trade("buy", 6, 20.0, D0304, security_id="SEC_T")
    terms = {
        **_MERGER,
        "merger_kind": "stock",
        "acquirer_security_id": "SEC_A",
        "share_ratio": 0.05,
        "acquirer_price": 100.0,
        "fractional_shares": 0.5,
    }
    book.adjust("corporate_action_cash", D0307, "SEC_T", quantity=-10, cash=100, explanation=terms)
    book.adjust("corporate_action_cash", D0310, "SEC_T", cash=50, explanation=_MERGER)

    lot_list, disposals, _ = book.run(D0310)
    assert _of("SEC_A", lot_list) == []
    assert [(d.quantity, d.proceeds, d.realised_pnl) for d in disposals] == [
        (4.0, 60.0, -20.0),
        (6.0, 90.0, -30.0),
    ]


def test_band_removal_followed_by_merger_cash_is_one_disposal_of_both() -> None:
    """A band removal (no merger id) of ten shares at basis 200 took 40; the
    merger then pays 150 more. The sale is one disposal of 190 proceeds."""
    book = Book()
    book.trade("buy", 10, 20.0, D0303, security_id="SEC_T")
    book.adjust("corporate_action_cash", D0307, "SEC_T", quantity=-10, cash=40)
    book.adjust("corporate_action_cash", D0310, "SEC_T", cash=150, explanation=_MERGER)

    _, disposals, _ = book.run(D0310)
    assert [(d.quantity, d.proceeds, d.realised_pnl) for d in disposals] == [(10.0, 190.0, -10.0)]


def test_merger_cash_with_no_removal_at_all_holds_back_lots() -> None:
    """Merger cash that no removal row of the name precedes is never dropped."""
    book = Book()
    book.trade("buy", 10, 20.0, D0303, security_id="SEC_T")
    book.adjust("corporate_action_cash", D0310, "SEC_T", cash=150, explanation=_MERGER)

    with pytest.raises(LotLedgerError, match="no merger removal"):
        book.run(D0310)


def test_later_stock_and_cash_boot_is_sold_not_cil() -> None:
    """Ten shares (basis 200) get four acquirer shares at 60 and 30 cash
    now, 30 cash later: the cash due is 60 and no fraction is due. All 60 is
    boot, f = 60/(60+240) = .2, so 2 shares sell 40 basis for 60 and four
    acquirer shares keep 160."""
    book = Book()
    book.trade("buy", 10, 20.0, D0303, security_id="SEC_T")
    terms = {
        **_MERGER,
        "merger_kind": "stock_and_cash",
        "acquirer_price": 60.0,
        "fractional_shares": 0.0,
        "same_session_cil_cash": 0.0,
        "merger_cash_due": 60.0,
    }
    book.adjust("corporate_action_cash", D0307, "SEC_T", quantity=-10, cash=30, explanation=terms)
    book.adjust("merger_receipt", D0307, "SEC_A", quantity=4, explanation=_MERGER)
    book.adjust("corporate_action_cash", D0310, "SEC_T", cash=30, explanation=_MERGER)

    lot_list, disposals, _ = book.run(D0310)
    assert [(r.quantity, r.cost_basis) for r in _of("SEC_A", lot_list)] == [(4.0, 160.0)]
    assert [(d.quantity, d.proceeds, d.realised_pnl) for d in disposals] == [(2.0, 60.0, 20.0)]


def test_later_boot_with_float_rounding_and_no_fraction_is_all_boot() -> None:
    """14 shares x 52.14 = 729.96 due; 6.06 paid at removal and the float
    remainder 723.9000000000001 later. No fraction is due, so the 1e-13 over
    the exact remainder is boot, not CIL, and the rebuild does not raise."""
    book = Book()
    book.trade("buy", 14, 20.0, D0303, security_id="SEC_T")
    terms = {
        **_MERGER,
        "merger_kind": "stock_and_cash",
        "acquirer_price": 60.0,
        "fractional_shares": 0.0,
        "same_session_cil_cash": 0.0,
        "merger_cash_due": 729.96,
    }
    book.adjust("corporate_action_cash", D0307, "SEC_T", quantity=-14, cash=6.06, explanation=terms)
    book.adjust("merger_receipt", D0307, "SEC_A", quantity=7, explanation=_MERGER)
    book.adjust(
        "corporate_action_cash", D0310, "SEC_T", cash=723.9000000000001, explanation=_MERGER
    )

    _, disposals, _ = book.run(D0310)
    assert len(disposals) == 1
    assert round(disposals[0].proceeds, 6) == 729.96


def test_later_cash_beyond_the_boot_due_is_cil() -> None:
    """As above with 40 later: 30 completes the 60 boot (sells 40 basis for
    60) and the last 10 sells one of the five fractional shares, f = 1/5 of
    the carried 160 basis = 32: realised 10 - 32."""
    book = Book()
    book.trade("buy", 10, 20.0, D0303, security_id="SEC_T")
    terms = {
        **_MERGER,
        "merger_kind": "stock_and_cash",
        "acquirer_price": 60.0,
        "fractional_shares": 1.0,
        "same_session_cil_cash": 0.0,
        "merger_cash_due": 60.0,
    }
    book.adjust("corporate_action_cash", D0307, "SEC_T", quantity=-10, cash=30, explanation=terms)
    book.adjust("merger_receipt", D0307, "SEC_A", quantity=4, explanation=_MERGER)
    book.adjust("corporate_action_cash", D0310, "SEC_T", cash=40, explanation=_MERGER)

    lot_list, disposals, _ = book.run(D0310)
    assert [(r.quantity, r.cost_basis) for r in _of("SEC_A", lot_list)] == [(4.0, 128.0)]
    assert [(d.proceeds, d.realised_pnl) for d in disposals] == [(60.0, 20.0), (10.0, -22.0)]


def test_same_session_stock_and_cash_cil_sells_fraction_separately() -> None:
    """Cash 90 contains 60 boot and 30 CIL. With four whole and one
    fractional share, boot sells 40 basis; CIL then sells 32 carried basis.
    The four acquirer shares retain 128 basis."""
    book = Book()
    book.trade("buy", 10, 20.0, D0303, security_id="SEC_T")
    terms = {
        **_MERGER,
        "merger_kind": "stock_and_cash",
        "acquirer_price": 60.0,
        "fractional_shares": 1.0,
        "same_session_cil_cash": 30.0,
    }
    book.adjust("corporate_action_cash", D0307, "SEC_T", quantity=-10, cash=90, explanation=terms)
    book.adjust("merger_receipt", D0307, "SEC_A", quantity=4, explanation=_MERGER)

    lot_list, disposals, _ = book.run(D0310)
    assert [(r.quantity, r.cost_basis) for r in _of("SEC_A", lot_list)] == [(4.0, 128.0)]
    assert [(d.proceeds, d.realised_pnl) for d in disposals] == [(60.0, 20.0), (30.0, -2.0)]


def test_later_cil_date_is_independent_of_adjustment_input_order() -> None:
    """Two later payments of 20 and 30 sell the same half share. The sale
    is stamped on the latest payment, March 12, whichever row arrived first."""
    book = Book()
    book.trade("buy", 10, 50.0, D0303, security_id="SEC_T")
    terms = {**_MERGER, "merger_kind": "stock", "fractional_shares": 0.5}
    book.adjust("corporate_action_cash", D0307, "SEC_T", quantity=-10, cash=0, explanation=terms)
    book.adjust("merger_receipt", D0307, "SEC_A", quantity=2, explanation=_MERGER)
    book.adjust("corporate_action_cash", D0310, "SEC_T", cash=20, explanation=_MERGER)
    book.adjust("corporate_action_cash", D0312, "SEC_T", cash=30, explanation=_MERGER)

    first = book.run(D0312)
    reversed_order = book.run(D0312, adjustments=list(reversed(book.adjustments)))
    assert first == reversed_order
    assert first[1][0].trade_date_local == D0312


def test_open_lots_report_the_ledgers_own_open_basis_once_after_a_merger_boot() -> None:
    """The stock-and-cash merger above: open basis 64 + 96 = 160 in 1.6 + 2.4
    acquirer shares. The boot's basis (40) came off the lots once, though the
    disposals also record it (#2247). `rebuild`'s own result is unchanged."""
    book = Book()
    book.trade("buy", 4, 20.0, D0303, security_id="SEC_T")
    book.trade("buy", 6, 20.0, D0304, security_id="SEC_T")
    terms = {
        **_MERGER,
        "merger_kind": "stock_and_cash",
        "acquirer_security_id": "SEC_A",
        "share_ratio": 0.4,
        "acquirer_price": 60.0,
        "fractional_shares": 0.0,
    }
    book.adjust("corporate_action_cash", D0307, "SEC_T", quantity=-10, cash=60, explanation=terms)
    book.adjust("merger_receipt", D0307, "SEC_A", quantity=4, explanation=_MERGER)
    held = lots.open_lots(
        book.fills,
        book.orders,
        {},
        ACCOUNT,
        adjustments=book.adjustments,
        actions=_splits(),
        through=D0310,
        symbols=SYMBOLS,
    )
    assert [(h.security_id, h.quantity, h.basis) for h in held] == [
        ("SEC_A", 1.6, 64.0),
        ("SEC_A", 2.4, 96.0),
    ]


def test_open_lots_drop_a_sold_lot_and_keep_the_rest_of_a_partial_one() -> None:
    """Buy 10 @ 100, buy 10 @ 110, sell 15: lot 1 is gone, lot 2 holds 5 with
    basis 550; a split 2:1 after scales the quantity (10) and keeps the basis."""
    book = Book()
    book.trade("buy", 10, 100.0, D0303)
    book.trade("buy", 10, 110.0, D0304)
    book.trade("sell", 15, 120.0, D0305)
    held = lots.open_lots(
        book.fills, book.orders, {}, ACCOUNT, actions=_splits(("SEC_X", D0307, 2.0)), through=D0310
    )
    assert [(h.lot_id, h.quantity, h.basis, h.position_side) for h in held] == [
        (2, 10.0, 550.0, "long")
    ]


# --- true-ups (paper spec amendment 2026-10-26 #2266, items 6 and 9) -------------------


@pytest.mark.parametrize("kind", ["auto_true_up", "owner_true_up"])
def test_a_positive_true_up_opens_a_zero_basis_lot_that_is_never_a_replacement(kind: str) -> None:
    """Hand values:

      03-03 buy 10 @ 50                        lot 1: basis 500
      03-04 true-up to 12.5 (delta +2.5)       lot 2: 2.5 shares, basis 0, dated 03-04
      03-05 sell 12.5 @ 40                     lot 1: pnl -100; lot 2: pnl +100
    Lot 2 was bought by nobody, so it is no wash-sale replacement for lot 1's loss.
    """
    book = Book()
    book.trade("buy", 10, 50.0, D0303)
    book.adjust(kind, D0304, "SEC_X", quantity=2.5, explanation={"broker_quantity": 12.5})
    book.trade("sell", 12.5, 40.0, D0305)

    lot_list, disposals, flags = book.run(D0305)

    assert [(lot.quantity, lot.cost_basis, lot.trade_date_local) for lot in lot_list] == [
        (10.0, 500.0, D0303),
        (2.5, 0.0, D0304),
    ]
    assert [(d.lot_id, d.quantity, d.proceeds, d.realised_pnl) for d in disposals] == [
        (1, 10.0, 400.0, -100.0),
        (2, 2.5, 100.0, 100.0),
    ]
    assert flags == []


def test_a_negative_true_up_closes_lots_fifo_at_zero_proceeds() -> None:
    """Hand values:

      03-03 buy 4 @ 50                         lot 1: basis 200
      03-04 buy 6 @ 60                         lot 2: basis 360
      03-05 true-up to 7 (delta -3)            lot 1: 3 shares closed, proceeds 0, pnl -150
    The cash, if any, is booked by its own row, so the disposal carries none.
    """
    book = Book()
    book.trade("buy", 4, 50.0, D0303)
    book.trade("buy", 6, 60.0, D0304)
    book.adjust("auto_true_up", D0305, "SEC_X", quantity=-3.0, explanation={"broker_quantity": 7.0})

    _, disposals, _ = book.run(D0305)

    assert [(d.lot_id, d.quantity, d.proceeds, d.realised_pnl) for d in disposals] == [
        (1, 3.0, 0.0, -150.0)
    ]


def test_a_true_up_is_judged_against_its_absolute_quantity_not_its_delta() -> None:
    """A split with an earlier ex-date that lands after the true-up was written is
    already in the broker's figure. Hand values:

      03-03 buy 10 @ 50
      03-04 true-up to 21 (written when the ledger held 10 and the broker 21: delta +11)
      split 2:1, ex-date 03-04 (known late): lot 1 becomes 20 shares
    The lots must total the absolute 21, so the true-up opens 1 share, not 11.
    """
    book = Book()
    book.trade("buy", 10, 50.0, D0303)
    book.adjust(
        "auto_true_up", D0304, "SEC_X", quantity=11.0, explanation={"broker_quantity": 21.0}
    )

    lot_list, _, _ = book.run(D0304, _splits(("SEC_X", D0304, 2.0)))

    assert sum(lot.quantity for lot in lot_list) == 21.0
    assert [lot.quantity for lot in lot_list] == [20.0, 1.0]


def test_a_cash_only_true_up_has_no_lot_effect_and_a_quantity_one_needs_the_absolute() -> None:
    book = Book()
    book.trade("buy", 10, 50.0, D0303)
    book.adjust("auto_true_up", D0304, "SEC_X", cash=10.0)
    lot_list, disposals, _ = book.run(D0304)
    assert [lot.quantity for lot in lot_list] == [10.0] and disposals == []

    book.adjust("auto_true_up", D0305, "SEC_X", quantity=1.0)
    with pytest.raises(ValueError, match="broker_quantity"):
        book.run(D0305)


def test_a_true_up_on_an_open_short_lot_raises() -> None:
    book = Book()
    _short_open(book)
    book.adjust("auto_true_up", D0305, "SEC_X", quantity=1.0, explanation={"broker_quantity": 1.0})
    with pytest.raises(LotLedgerError):
        book.run(D0305)


def test_a_fill_journaled_after_a_true_up_on_its_session_is_not_overwritten() -> None:
    """Safety/quant pass 1: the true-up states the name as of its broker read; a
    fill stamped after it, on the same session, adds to it. Hand values:

      03-03 buy 10 @ 50
      03-04 true-up to 10.01 (stamped 03-04 close + 30 min)
      03-04 buy 5 @ 40 journaled after the true-up -> 15.01 shares, nothing closed
    """
    book = Book()
    book.trade("buy", 10, 50.0, D0303)
    book.adjust(
        "auto_true_up", D0304, "SEC_X", quantity=0.01, explanation={"broker_quantity": 10.01}
    )
    book.trade("buy", 5, 40.0, D0304)
    late = book.fills[-1]
    book.fills[-1] = OrderedFill(
        FillRow(**{**late.fill.__dict__, "known_at": session_close(D0304) + timedelta(hours=2)}),
        late.side,
        late.security_id,
        late.symbol,
        late.window_id,
        late.run_id,
    )

    lot_list, disposals, _ = book.run(D0304)

    assert disposals == []
    assert sum(lot.quantity for lot in lot_list) == pytest.approx(15.01)


def test_a_fill_between_two_true_ups_of_one_session_is_counted_once() -> None:
    """Steps 4 and 8 both reconcile on one session: true-up to 10.01, a buy of 5,
    then a second true-up to 15.01 (its broker read holds the buy). Ordered by
    their stamps the lots total 15.01, not 20.01."""
    book = Book()
    book.trade("buy", 10, 50.0, D0303)
    book.adjust(
        "auto_true_up", D0304, "SEC_X", quantity=0.01, explanation={"broker_quantity": 10.01}
    )
    book.trade("buy", 5, 40.0, D0304)
    late = book.fills[-1]
    book.fills[-1] = OrderedFill(
        FillRow(**{**late.fill.__dict__, "known_at": session_close(D0304) + timedelta(hours=1)}),
        late.side,
        late.security_id,
        late.symbol,
        late.window_id,
        late.run_id,
    )
    second = book.adjust(
        "auto_true_up", D0304, "SEC_X", quantity=0.0, explanation={"broker_quantity": 15.01}
    )
    book.adjustments[-1] = AdjustmentRow(
        **{**second.__dict__, "known_at": session_close(D0304) + timedelta(hours=2)}
    )

    lot_list, _, _ = book.run(D0304)

    assert sum(lot.quantity for lot in lot_list) == pytest.approx(15.01)

"""True-up replay: a fill stamped at or before a true-up is inside its figure (#2479).

A true-up states the broker's absolute quantity, which holds every fill
known at or before its own `known_at`. So a fill whose stamp ties a true-up is
replayed *before* it, whatever the row ids (the rule that never counts a fill
twice), and an order whose fills fall on both sides of a true-up is split there
in the lots too, so the lots total what the positions ledger holds.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import date, timedelta

import pytest

from tradepartner.calendar import session_close
from tradepartner.execution import lots
from tradepartner.execution.ledger import from_journal
from tradepartner.store.journal import AdjustmentRow, FillRow, OrderedFill

from .test_lots_adjustments import ACCOUNT, D0303, D0304, SYMBOLS, Book, _splits

SEC = "SEC_X"


def _stamp(hours: int):  # type: ignore[no-untyped-def]
    return session_close(D0304) + timedelta(hours=hours)


def _restamp_fill(book: Book, index: int, hours: int, *, fill_id: int | None = None) -> None:
    item = book.fills[index]
    row: FillRow = replace(
        item.fill,
        known_at=_stamp(hours),
        fill_id=item.fill.fill_id if fill_id is None else fill_id,
    )
    book.fills[index] = OrderedFill(
        row, item.side, item.security_id, item.symbol, item.window_id, item.run_id
    )


def _true_up(book: Book, absolute: float, hours: int, *, adjustment_id: int | None = None) -> None:
    row = book.adjust(
        "auto_true_up", D0304, SEC, quantity=0.0, explanation={"broker_quantity": absolute}
    )
    book.adjustments[-1] = replace(
        row,
        known_at=_stamp(hours),
        quantity=absolute - 0.0,
        adjustment_id=row.adjustment_id if adjustment_id is None else adjustment_id,
    )


def _ledger_quantity(book: Book) -> float:
    ledger = from_journal(
        book.fills,
        book.orders,
        book.adjustments,
        _splits(),
        None,
        1_000_000.0,
        D0304,
        window_id=1,
        quantity_tolerance=0.0,
    )
    return ledger.positions.get(SEC, 0.0)


def _lots_quantity(book: Book) -> float:
    """What the open lots of the name still hold (a closed lot reports its
    acquired quantity, so `rebuild`'s rows would overstate it)."""
    held = lots.open_lots(
        book.fills,
        book.orders,
        {},
        ACCOUNT,
        adjustments=book.adjustments,
        actions=_splits(),
        through=D0304,
        symbols=SYMBOLS,
    )
    return sum(lot.quantity for lot in held if lot.security_id == SEC)


@pytest.mark.parametrize(
    ("fill_id", "adjustment_id"), [(1_000_001, 1_000_002), (1_000_002, 1_000_001)]
)
def test_a_fill_tied_with_a_second_true_up_is_counted_once(
    fill_id: int, adjustment_id: int
) -> None:
    """Buy 10; true-up to 10.01 at +1h; a 5-share fill and a second true-up to
    15.01 both stamped +2h. The second figure holds the fill: 15.01, not 20.01,
    for any ids."""
    book = Book()
    book.trade("buy", 10, 50.0, D0303)
    _true_up(book, 10.01, 1)
    book.trade("buy", 5, 40.0, D0304)
    _restamp_fill(book, -1, 2, fill_id=fill_id)
    _true_up(book, 15.01, 2, adjustment_id=adjustment_id)

    assert _ledger_quantity(book) == pytest.approx(15.01)
    assert _lots_quantity(book) == pytest.approx(15.01)


def test_a_resumed_order_with_fills_on_both_sides_of_a_true_up_is_not_inflated() -> None:
    """Buy 5 (fill known +1h), a true-up to 15.01 at +2h holds it, then a
    5-share implied residual of the same order known +3h: 20.01 in both."""
    book = Book()
    book.trade("buy", 10, 50.0, D0303)
    book.trade("buy", 5, 40.0, D0304)
    _restamp_fill(book, -1, 1)
    _true_up(book, 15.01, 2)
    first = book.fills[-1]
    residual = replace(
        first.fill,
        fill_id=3_000_000,
        broker_fill_id=None,
        price_implied=True,
        known_at=_stamp(3),
        source="implied",
    )
    book.fills.append(
        OrderedFill(
            residual, first.side, first.security_id, first.symbol, first.window_id, first.run_id
        )
    )

    assert _ledger_quantity(book) == pytest.approx(20.01)
    assert _lots_quantity(book) == pytest.approx(20.01)


def _random_book(rng: random.Random) -> Book:
    book = Book()
    ids = iter(rng.sample(range(2_000_000, 2_000_100), 100))
    book.trade("buy", 10, 50.0, D0303)
    for _ in range(rng.randint(1, 4)):
        book.trade("buy", rng.randint(1, 5), 40.0, D0304)
        _restamp_fill(book, -1, rng.randint(1, 4), fill_id=next(ids))
        if rng.random() < 0.5:  # a resumed order: a second, implied, fill
            first = book.fills[-1]
            book.fills.append(
                OrderedFill(
                    replace(
                        first.fill,
                        fill_id=next(ids),
                        broker_fill_id=None,
                        price_implied=True,
                        known_at=_stamp(rng.randint(1, 4)),
                        source="implied",
                    ),
                    first.side,
                    first.security_id,
                    first.symbol,
                    first.window_id,
                    first.run_id,
                )
            )
    for _ in range(rng.randint(1, 3)):
        _true_up(book, 10 + rng.randint(0, 20) + 0.01, rng.randint(1, 4), adjustment_id=next(ids))
    return book


@pytest.mark.parametrize("seed", range(120))
def test_lots_total_the_positions_ledger_after_true_ups(seed: int) -> None:
    """Property: whatever the stamps, ties and ids, the open lots of a name
    total what the positions ledger holds for it."""
    book = _random_book(random.Random(seed))
    assert _lots_quantity(book) == pytest.approx(_ledger_quantity(book))


_ = (SYMBOLS, date, AdjustmentRow)

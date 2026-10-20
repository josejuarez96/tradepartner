"""#516 build edges beside the locked `test_lots_adjustments.py` (never edited):
a stock merger keeps the target's wash sale, and a second receipt of one merger
id halts instead of being dropped."""

from __future__ import annotations

import pytest

from .test_lots_adjustments import (
    _MERGER,
    D0303,
    D0304,
    D0305,
    D0307,
    D0312,
    Book,
    LotLedgerError,
    _merge,
)


def test_a_stock_merger_keeps_the_targets_wash_sale() -> None:
    """Hand values: 03-03 buy 10 SEC_T @ 30; 03-04 sell 5 @ 20 (loss 50); 03-05
    buy 5 @ 20 (a replacement inside the window); 03-07 the merger re-keys the
    open lots to SEC_A. The loss is still flagged against the repurchase lot (its quantity is in the
    acquirer's units after the merger; unit normalisation is #1980)."""
    book = Book()
    book.trade("buy", 10, 30.0, D0303, security_id="SEC_T")
    book.trade("sell", 5, 20.0, D0304, security_id="SEC_T")
    book.trade("buy", 5, 20.0, D0305, security_id="SEC_T")
    _merge(book, D0307, 10.0, 5.0)

    _, disposals, flags = book.run(D0312)

    assert [(d.realised_pnl, d.quantity) for d in disposals] == [(-50.0, 5.0)]
    assert [(f.disposal_id, f.replacement_lot_id) for f in flags] == [(1, 2)]


def test_a_second_receipt_of_one_merger_raises() -> None:
    book = Book()
    book.trade("buy", 10, 30.0, D0303, security_id="SEC_T")
    _merge(book, D0307, 10.0, 5.0)
    book.adjust("merger_receipt", D0307, "SEC_A", quantity=1.0, explanation=_MERGER)

    with pytest.raises(LotLedgerError, match="two merger_receipt rows"):
        book.run(D0312)

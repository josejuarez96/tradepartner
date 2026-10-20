"""#1980: a wash-sale match normalises the disposal and the replacement lot to
one share unit, so a split or a stock merger between them never mixes units.

A disposal keeps the units of its own sale (module docstring), while a lot
reports today's units (post-split, or the acquirer's after a stock merger). The
scan compares the two, so it must bring the disposal - and the shares the same
sale closed - into the replacement lot's units. These are the pre-split and
pre-merger disposals the locked `test_lots_adjustments.py` left unpinned; a
post-split disposal (its sale already in today's units) is pinned there and is
unaffected. Hand values are in each docstring.
"""

from __future__ import annotations

from .test_lots_adjustments import (
    D0303,
    D0304,
    D0305,
    D0307,
    D0312,
    Book,
    _merge,
    _splits,
)


def test_a_loss_sold_before_a_split_matches_in_one_unit() -> None:
    """Hand values:

      03-03 buy 20 @ 100   lot 1: basis 2000
      03-04 buy  5 @ 100   lot 2: basis 500 (a replacement)
      03-05 sell 20 @ 40   closes lot 1 whole: proceeds 800, pnl -1200 (60 a share)
      03-07 2:1 split       lot 2 holds 10 shares today

    The sale's 20 pre-split shares are 40 today, and lot 2 holds 10, so it
    replaces 10 of the 40: disallowed 1200 x 10/40 = 300. Mixed units match all
    10 of lot 2 against 20 and disallow 600.
    """
    book = Book()
    book.trade("buy", 20, 100.0, D0303)
    book.trade("buy", 5, 100.0, D0304)
    book.trade("sell", 20, 40.0, D0305)

    _, disposals, flags = book.run(D0312, _splits(("SEC_X", D0307, 2.0)))

    assert [(d.lot_id, d.quantity, d.proceeds, d.realised_pnl) for d in disposals] == [
        (1, 20.0, 800.0, -1200.0)
    ]
    assert [
        (f.disposal_id, f.replacement_lot_id, f.matched_quantity, f.disallowed_amount)
        for f in flags
    ] == [(1, 2, 10.0, 300.0)]


def test_a_loss_sold_before_a_stock_merger_matches_in_one_unit() -> None:
    """Hand values:

      03-03 buy 10 SEC_T @ 30   lot 1: basis 300
      03-04 buy  4 SEC_T @ 25   lot 2: basis 100 (a replacement)
      03-05 sell 10 SEC_T @ 20  closes lot 1 whole: proceeds 200, pnl -100 (10 a share)
      03-07 merger 4 SEC_T -> 2 SEC_A at 0.5: lot 2 holds 2 SEC_A, basis 100

    The sale's 10 target shares are 5 acquirer shares, and lot 2 holds 2, so it
    replaces 2 of the 5: disallowed 100 x 2/5 = 40. Mixed units match 2 against
    10 and disallow 20.
    """
    book = Book()
    book.trade("buy", 10, 30.0, D0303, security_id="SEC_T")
    book.trade("buy", 4, 25.0, D0304, security_id="SEC_T")
    book.trade("sell", 10, 20.0, D0305, security_id="SEC_T")
    _merge(book, D0307, 4.0, 2.0)

    _, disposals, flags = book.run(D0312)

    assert [(d.lot_id, d.quantity, d.proceeds, d.realised_pnl) for d in disposals] == [
        (1, 10.0, 200.0, -100.0)
    ]
    assert [
        (f.disposal_id, f.replacement_lot_id, f.matched_quantity, f.disallowed_amount)
        for f in flags
    ] == [(1, 2, 2.0, 40.0)]


def test_the_shares_the_same_sale_closed_are_scaled_too() -> None:
    """Hand values:

      03-03 buy 10 @ 100   lot 1: basis 1000
      03-05 sell 6 @ 90    closes 6 of lot 1: proceeds 540, pnl -60 (10 a share)
      03-07 2:1 split      lot 1 holds 8 today (open basis 400)

    The sale's 6 pre-split shares are 12 today, and the 6 it closed on lot 1 are
    12 too, leaving 8 replacement shares there. Eight of the twelve loss-shares
    are replaced: disallowed 60 x 8/12 = 40. Leaving the same-sale shares in
    their own units would leave 20 - 6 = 14 and disallow the whole 60.
    """
    book = Book()
    book.trade("buy", 10, 100.0, D0303)
    book.trade("sell", 6, 90.0, D0305)

    _, _, flags = book.run(D0312, _splits(("SEC_X", D0307, 2.0)))

    assert [(f.replacement_lot_id, f.matched_quantity, f.disallowed_amount) for f in flags] == [
        (1, 8.0, 40.0)
    ]

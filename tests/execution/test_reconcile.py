"""Reconciliation compare, pure (Phase 4 plan T55, #321).

Every "Reconciliation on the fake" criterion that needs no run, as a pure case
over hand-built inputs: each mismatch kind; each explanation (a split through
the T51 ledger from actions known at close(S-1), an ended listing with its
`corporate_action_cash`, a spin-off child with its `spinoff_receipt`, a credited
dividend with its `dividend_cash`); a split known only after close(S-1) not
accepted; the `pending_unresolved` and `fills_lagging` allowances and their
bounds; status precedence; lagging ids on every row; the split on the session
with no jump at the compare level. The literal check on `reconcile.py` is T50's.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime

import polars as pl
import pytest

from tradepartner.adapters.broker import Account, CashActivity, Order, OrderStatus, Position, Side
from tradepartner.config import RiskConfig
from tradepartner.execution.ledger import Ledger, from_journal
from tradepartner.execution.reconcile import (
    Explanations,
    JournalOpenOrder,
    Reconciliation,
    compare,
)
from tradepartner.store.journal import FillRow, OrderedFill, OrderRow, PaperWindowRow

S = date(2026, 10, 7)
PRIOR = date(2026, 10, 6)
AT = datetime(2026, 10, 7, 13, 0, tzinfo=UTC)
# Quantity tolerance 1e-6; cash tolerance 0.01, the strict half-`cash` cent the
# bound scales per fill (ADR 0010 amendment 2026-10-12, #1583/T183; it reverts
# the #1290 whole-dollar 1.26 band): the cash cases below step past it by whole
# dollars, except the bound and fee cases.
FROZEN = RiskConfig()
PREFIX = "tp"
SYMBOLS = {"SEC_A": "AAA", "SEC_B": "BBB", "SEC_C": "CCC", "SEC_KID": "KID"}
WINDOW = PaperWindowRow(
    window_id=1,
    hypothesis_id=1,
    first_rebalance_session=date(2026, 9, 30),
    account_id="acct-1",
    starting_cash=10_000.0,
    starting_equity=10_000.0,
    code_version="test",
    started_at=AT,
    frozen_json="{}",
    frozen_sha256="0" * 64,
    known_at=AT,
    ingested_at=AT,
)


def _explanations(**overrides: object) -> Explanations:
    values: dict[str, object] = {
        "symbols": SYMBOLS,
        "ended": frozenset(),
        "spinoffs": {},
        "dividends": {},
        "reference_prices": {"SEC_A": 100.0, "SEC_B": 50.0, "SEC_C": 20.0, "SEC_KID": 5.0},
    }
    values.update(overrides)
    return Explanations(**values)  # type: ignore[arg-type]


def _account(cash: float, account_id: str = "acct-1") -> Account:
    return Account(account_id=account_id, cash=cash, buying_power=cash, equity=cash, as_of=AT)


def _positions(**quantities: float) -> dict[str, Position]:
    return {symbol: Position(symbol=symbol, quantity=q) for symbol, q in quantities.items()}


def _order_row(coid: str, security_id: str, side: str, **size: float) -> OrderRow:
    return OrderRow(
        client_order_id=coid,
        decision_id=1,
        run_id=1,
        session=S,
        attempt=1,
        phase=side,
        security_id=security_id,
        symbol=SYMBOLS[security_id],
        side=side,
        sells_in_flight_at_submit=False,
        known_at=AT,
        ingested_at=AT,
        **size,
    )


def _reading(
    row: OrderRow,
    status: OrderStatus,
    filled: float | None = None,
    avg: float | None = None,
) -> Order:
    return Order(
        client_order_id=row.client_order_id,
        symbol=row.symbol,
        side=Side(row.side),
        notional=row.notional,
        quantity=row.quantity,
        status=status,
        submitted_at=AT,
        broker_order_id="b-" + row.client_order_id,
        filled_quantity=filled,
        filled_avg_price=avg,
        filled_at=AT if status is OrderStatus.FILLED else None,
    )


def _run(
    ledger: Ledger,
    positions: Mapping[str, Position],
    cash: float,
    *,
    open_orders: Sequence[Order] = (),
    journal_open: Sequence[JournalOpenOrder] = (),
    explanations: Explanations | None = None,
    account_id: str = "acct-1",
) -> Reconciliation:
    return compare(
        ledger,
        positions,
        open_orders,
        journal_open,
        _account(cash, account_id),
        explanations or _explanations(),
        WINDOW,
        FROZEN,
        order_id_prefix=PREFIX,
    )


def _ledger(cash: float = 1_000.0, *, fills_since_base: int = 0, **positions: float) -> Ledger:
    return Ledger(positions=positions, cash=cash, through=S, fills_since_base=fills_since_base)


def _kinds(result: Reconciliation) -> list[str]:
    return [m.kind for m in result.mismatches]


# --- ok and each mismatch kind -------------------------------------------------


def test_matching_books_are_ok_and_record_the_broker_cash() -> None:
    result = _run(_ledger(1_000.0, SEC_A=10.0), _positions(AAA=10.0), 1_000.004)

    assert result.status == "ok"
    assert result.broker_cash == 1_000.004
    assert result.mismatches == ()
    assert result.adjustments == ()


@pytest.mark.parametrize(
    ("ledger", "positions", "cash", "kind"),
    [
        (_ledger(SEC_A=10.0), _positions(AAA=10.0, BBB=3.0), 1_000.0, "broker_only_position"),
        (_ledger(SEC_A=10.0, SEC_B=3.0), _positions(AAA=10.0), 1_000.0, "ledger_only_position"),
        (_ledger(SEC_A=10.0), _positions(AAA=10.00001), 1_000.0, "quantity"),
        (_ledger(SEC_A=10.0), _positions(AAA=10.0), 1_002.0, "cash"),
        (_ledger(SEC_A=10.0), _positions(AAA=10.0, ZZZ=1.0), 1_000.0, "unknown_symbol"),
    ],
)
def test_each_position_and_cash_mismatch(
    ledger: Ledger, positions: dict[str, Position], cash: float, kind: str
) -> None:
    result = _run(ledger, positions, cash)

    assert result.status == "mismatch"
    assert _kinds(result) == [kind]
    assert result.broker_cash is None  # never a re-base
    assert kind in json.loads(result.mismatches_json)["mismatches"][0]["kind"]


@pytest.mark.parametrize(
    ("fills", "cash", "status"),
    [
        (0, 1_000.01, "ok"),  # the bare key, inclusive
        (0, 1_000.02, "mismatch"),
        (10, 1_000.06, "ok"),  # 0.01 x (1 + 10/2)
        (10, 1_000.07, "mismatch"),
    ],
)
def test_the_cash_bound_scales_with_the_ledgers_fills(fills: int, cash: float, status: str) -> None:
    """#1583, T183: the strict bound is `reconcile_cash_tolerance` x
    (1 + fills_since_base / 2), inclusive — a cent of rounding per fill per side
    replaces the 1.26 whole-dollar band (#1290), which T183 removes."""
    ledger = _ledger(1_000.0, fills_since_base=fills, SEC_A=10.0)
    result = _run(ledger, _positions(AAA=10.0), cash)

    assert result.status == status
    assert _kinds(result) == ([] if status == "ok" else ["cash"])


def test_a_window_frozen_at_the_old_tolerance_keeps_it() -> None:
    """#1290: the band is the window's frozen value, not the default; a window that
    froze 0.01 (H1's window 1) still finds a $1.25 gap a mismatch."""
    result = compare(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=10.0),
        (),
        (),
        _account(1_001.25),
        _explanations(),
        WINDOW,
        RiskConfig(reconcile_cash_tolerance=0.01),
        order_id_prefix=PREFIX,
    )

    assert _kinds(result) == ["cash"]


def test_a_different_account_id_is_a_mismatch() -> None:
    result = _run(_ledger(SEC_A=1.0), _positions(AAA=1.0), 1_000.0, account_id="other")
    assert _kinds(result) == ["account_id"]


def test_a_ledger_name_with_no_symbol_is_a_mismatch() -> None:
    result = _run(_ledger(SEC_Q=1.0), {}, 1_000.0)
    assert _kinds(result) == ["unknown_security"]


def test_a_foreign_prefix_order_is_a_mismatch() -> None:
    foreign = _reading(
        _order_row("owner-manual-1", "SEC_A", "buy", quantity=1.0), OrderStatus.ACCEPTED
    )
    result = _run(_ledger(SEC_A=1.0), _positions(AAA=1.0), 1_000.0, open_orders=[foreign])
    assert _kinds(result) == ["foreign_order"]


def test_an_own_prefix_order_the_journal_does_not_hold_open_is_a_mismatch() -> None:
    stray = _reading(
        _order_row("tp-20261007-SEC_A-buy-9", "SEC_A", "buy", quantity=1.0), OrderStatus.ACCEPTED
    )
    result = _run(_ledger(SEC_A=1.0), _positions(AAA=1.0), 1_000.0, open_orders=[stray])
    assert _kinds(result) == ["unexplained_open_order"]


def test_a_prefix_needs_its_separator() -> None:
    lookalike = _reading(_order_row("tpx-1", "SEC_A", "buy", quantity=1.0), OrderStatus.ACCEPTED)
    result = _run(_ledger(SEC_A=1.0), _positions(AAA=1.0), 1_000.0, open_orders=[lookalike])
    assert _kinds(result) == ["foreign_order"]


def test_a_journal_open_order_the_broker_holds_open_matches() -> None:
    row = _order_row("tp-20261007-SEC_A-buy-1", "SEC_A", "buy", notional=100.0)
    reading = _reading(row, OrderStatus.ACCEPTED)
    result = _run(
        _ledger(SEC_A=1.0),
        _positions(AAA=1.0),
        1_000.0,
        open_orders=[reading],
        journal_open=[
            JournalOpenOrder(
                row, pending=False, journaled_quantity=0.0, journaled_notional=0.0, reading=reading
            )
        ],
    )
    assert result.status == "ok"


def test_a_journal_open_order_the_broker_calls_open_but_does_not_list_is_a_mismatch() -> None:
    row = _order_row("tp-20261007-SEC_A-buy-1", "SEC_A", "buy", notional=100.0)
    reading = _reading(row, OrderStatus.ACCEPTED)
    result = _run(
        _ledger(SEC_A=1.0),
        _positions(AAA=1.0),
        1_000.0,
        journal_open=[
            JournalOpenOrder(
                row, pending=False, journaled_quantity=0.0, journaled_notional=0.0, reading=reading
            )
        ],
    )
    assert _kinds(result) == ["missing_open_order"]


def test_an_acknowledged_open_order_without_a_reading_is_a_mismatch() -> None:
    row = _order_row("tp-20261007-SEC_A-buy-1", "SEC_A", "buy", notional=100.0)
    result = _run(
        _ledger(SEC_A=1.0),
        _positions(AAA=1.0),
        1_000.0,
        journal_open=[
            JournalOpenOrder(
                row, pending=False, journaled_quantity=0.0, journaled_notional=0.0, reading=None
            )
        ],
    )
    assert _kinds(result) == ["missing_open_order"]


def test_a_journal_open_order_the_broker_finished_with_every_fill_collected_is_ok() -> None:
    row = _order_row("tp-20261007-SEC_A-buy-1", "SEC_A", "buy", quantity=2.0)
    reading = _reading(row, OrderStatus.FILLED, filled=2.0, avg=100.0)
    result = _run(
        _ledger(800.0, SEC_A=3.0),
        _positions(AAA=3.0),
        800.0,
        journal_open=[
            JournalOpenOrder(
                row,
                pending=False,
                journaled_quantity=2.0,
                journaled_notional=200.0,
                reading=reading,
            )
        ],
    )
    assert result.status == "ok"


def test_several_mismatches_are_all_listed() -> None:
    result = _run(_ledger(SEC_A=10.0), _positions(AAA=9.0, BBB=1.0), 990.0, account_id="x")
    assert sorted(_kinds(result)) == ["account_id", "broker_only_position", "cash", "quantity"]


# --- explanations --------------------------------------------------------------


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


def _journal_ledger(actions: pl.DataFrame) -> Ledger:
    """10 SEC_A bought on PRIOR, stated for S through the T51 ledger."""
    row = _order_row("tp-20261006-SEC_A-buy-1", "SEC_A", "buy", quantity=10.0)
    filled = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)
    fill = FillRow(
        fill_id=1,
        client_order_id=row.client_order_id,
        filled_at=filled,
        quantity=10.0,
        price=100.0,
        price_implied=False,
        broker_fill_id="bf-1",
        source="broker_feed",
        known_at=filled,
        ingested_at=filled,
    )
    live = OrderedFill(fill, "buy", "SEC_A", "AAA", 1, 1)
    return from_journal(
        [live],
        [row],
        [],
        actions,
        None,
        2_000.0,
        S,
        window_id=1,
        quantity_tolerance=FROZEN.reconcile_quantity_tolerance,
    )


def test_a_split_known_at_close_s_minus_1_explains_the_doubled_quantity() -> None:
    """The split on the session: the ledger for S applies the 2:1 split with
    `ex_date = S` known at close(S-1), so the broker's doubled quantity
    reconciles with no jump and no adjustment."""
    ledger = _journal_ledger(_splits(("SEC_A", S, 2.0)))

    result = _run(ledger, _positions(AAA=20.0), 1_000.0)

    assert result.status == "ok"
    assert result.adjustments == ()


def test_a_split_known_only_after_close_s_minus_1_is_not_accepted() -> None:
    """`live_actions_as_of(close(S-1))` does not hold the split, so the ledger
    keeps 10 shares and the broker's 20 is a mismatch (no look-ahead)."""
    ledger = _journal_ledger(_splits())

    result = _run(ledger, _positions(AAA=20.0), 1_000.0)

    assert _kinds(result) == ["quantity"]


def test_an_ended_listing_explains_a_removed_position_and_its_cash() -> None:
    result = _run(
        _ledger(1_000.0, SEC_A=10.0, SEC_B=4.0),
        _positions(AAA=10.0),
        1_190.0,  # 4 x 47.50 cash merger consideration
        explanations=_explanations(ended=frozenset({"SEC_B"})),
    )

    assert result.status == "ok"
    [adjustment] = result.adjustments
    assert (adjustment.kind, adjustment.security_id) == ("corporate_action_cash", "SEC_B")
    assert (adjustment.quantity, adjustment.cash) == (-4.0, pytest.approx(190.0))
    assert result.broker_cash == 1_190.0


def test_an_ended_listing_with_no_cash_is_explained_too() -> None:
    result = _run(
        _ledger(1_000.0, SEC_B=4.0),
        {},
        1_000.0,
        explanations=_explanations(ended=frozenset({"SEC_B"})),
    )
    assert result.status == "ok"
    assert [(a.quantity, a.cash) for a in result.adjustments] == [(-4.0, 0.0)]


def test_an_ended_listing_never_explains_lost_cash() -> None:
    result = _run(
        _ledger(1_000.0, SEC_B=4.0),
        {},
        900.0,
        explanations=_explanations(ended=frozenset({"SEC_B"})),
    )
    assert _kinds(result) == ["cash"]


def test_two_ended_listings_share_the_cash_by_reference_value() -> None:
    result = _run(
        _ledger(1_000.0, SEC_A=1.0, SEC_B=2.0),  # reference values 100 and 100
        {},
        1_150.0,
        explanations=_explanations(ended=frozenset({"SEC_A", "SEC_B"})),
    )
    assert result.status == "ok"
    assert sorted((a.security_id, a.cash) for a in result.adjustments) == [
        ("SEC_A", pytest.approx(75.0)),
        ("SEC_B", pytest.approx(75.0)),
    ]


def test_an_ended_listing_the_broker_still_holds_explains_nothing() -> None:
    result = _run(
        _ledger(1_000.0, SEC_B=4.0),
        _positions(BBB=4.0),
        1_000.0,
        explanations=_explanations(ended=frozenset({"SEC_B"})),
    )
    assert result.status == "ok"
    assert result.adjustments == ()


def test_a_spin_off_child_explains_a_new_position() -> None:
    result = _run(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=10.0, KID=2.5),
        1_000.0,
        explanations=_explanations(spinoffs={"SEC_KID": ("SEC_A", 0.25)}),
    )

    assert result.status == "ok"
    [adjustment] = result.adjustments
    assert (adjustment.kind, adjustment.security_id, adjustment.quantity, adjustment.cash) == (
        "spinoff_receipt",
        "SEC_KID",
        2.5,
        None,
    )


def test_a_spin_off_child_of_a_name_not_held_is_a_mismatch() -> None:
    result = _run(
        _ledger(1_000.0, SEC_B=1.0),
        _positions(BBB=1.0, KID=2.5),
        1_000.0,
        explanations=_explanations(spinoffs={"SEC_KID": ("SEC_A", 0.25)}),
    )
    assert _kinds(result) == ["broker_only_position"]


def test_a_credited_dividend_explains_the_cash() -> None:
    result = _run(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=10.0),
        1_004.5,
        explanations=_explanations(dividends={"SEC_A": 4.5}),
    )

    assert result.status == "ok"
    [adjustment] = result.adjustments
    assert (adjustment.kind, adjustment.security_id, adjustment.quantity) == (
        "dividend_cash",
        "SEC_A",
        None,
    )
    assert adjustment.cash == pytest.approx(4.5)


def test_a_dividend_not_credited_is_not_journaled() -> None:
    result = _run(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=10.0),
        1_000.0,
        explanations=_explanations(dividends={"SEC_A": 4.5}),
    )
    assert result.status == "ok"
    assert result.adjustments == ()


def test_a_cash_difference_other_than_the_dividend_is_a_mismatch() -> None:
    result = _run(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=10.0),
        1_006.0,
        explanations=_explanations(dividends={"SEC_A": 4.5}),
    )
    assert _kinds(result) == ["cash"]
    assert result.adjustments == ()


# --- pending and lagging allowances --------------------------------------------


def _lagging_buy(filled: float, journaled: float, avg: float = 100.0) -> JournalOpenOrder:
    row = _order_row("tp-20261007-SEC_A-buy-1", "SEC_A", "buy", notional=500.0)
    return JournalOpenOrder(
        row,
        pending=False,
        journaled_quantity=journaled,
        journaled_notional=journaled * avg,
        reading=_reading(row, OrderStatus.FILLED, filled=filled, avg=avg),
    )


def test_a_lagging_fill_explains_its_gap_without_rebasing() -> None:
    """The broker filled 5 at 100; the journal has 2. Broker 3 more shares
    and 300 less cash is explained."""
    result = _run(
        _ledger(1_000.0, SEC_A=12.0),
        _positions(AAA=15.0),
        700.0,
        journal_open=[_lagging_buy(5.0, 2.0)],
    )

    assert result.status == "fills_lagging"
    assert result.broker_cash is None
    assert result.lagging_ids == ("tp-20261007-SEC_A-buy-1",)
    assert json.loads(result.mismatches_json)["lagging"] == ["tp-20261007-SEC_A-buy-1"]


def test_a_lagging_gap_is_explained_up_to_its_bound_only() -> None:
    inside = _run(
        _ledger(1_000.0, SEC_A=12.0),
        _positions(AAA=14.0),
        800.0,
        journal_open=[_lagging_buy(5.0, 2.0)],
    )
    assert inside.status == "fills_lagging"
    beyond = _run(
        _ledger(1_000.0, SEC_A=12.0),
        _positions(AAA=16.0),
        600.0,
        journal_open=[_lagging_buy(5.0, 2.0)],
    )
    assert sorted(_kinds(beyond)) == ["cash", "quantity"]
    assert beyond.lagging_ids == ("tp-20261007-SEC_A-buy-1",)  # listed on every row
    wrong_way = _run(
        _ledger(1_000.0, SEC_A=12.0),
        _positions(AAA=11.0),
        1_100.0,
        journal_open=[_lagging_buy(5.0, 2.0)],
    )
    assert sorted(_kinds(wrong_way)) == ["cash", "quantity"]


def test_fills_within_the_quantity_tolerance_are_not_lagging() -> None:
    result = _run(
        _ledger(1_000.0, SEC_A=12.0),
        _positions(AAA=12.0),
        1_000.0,
        journal_open=[_lagging_buy(2.0 + 5e-7, 2.0)],
    )
    assert result.status == "ok"
    assert result.lagging_ids == ()


def _pending(coid: str, security_id: str, side: str, **size: float) -> JournalOpenOrder:
    return JournalOpenOrder(
        _order_row(coid, security_id, side, **size),
        pending=True,
        journaled_quantity=0.0,
        journaled_notional=0.0,
        reading=None,
    )


def test_a_pending_order_is_pending_unresolved_even_with_no_difference() -> None:
    pending = _pending("tp-20261007-SEC_B-buy-1", "SEC_B", "buy", notional=200.0)
    result = _run(_ledger(SEC_A=1.0), _positions(AAA=1.0), 1_000.0, journal_open=[pending])

    assert result.status == "pending_unresolved"
    assert result.pending_ids == ("tp-20261007-SEC_B-buy-1",)
    assert result.broker_cash is None


def test_a_pending_order_explains_its_symbol_and_cash_up_to_its_notional() -> None:
    pending = _pending("tp-20261007-SEC_B-buy-1", "SEC_B", "buy", notional=200.0)
    inside = _run(
        _ledger(1_000.0, SEC_A=1.0), _positions(AAA=1.0, BBB=3.9), 805.0, journal_open=[pending]
    )
    assert inside.status == "pending_unresolved"
    beyond = _run(
        _ledger(1_000.0, SEC_A=1.0), _positions(AAA=1.0, BBB=3.9), 795.0, journal_open=[pending]
    )
    assert _kinds(beyond) == ["cash"]
    other_symbol = _run(
        _ledger(1_000.0, SEC_A=1.0), _positions(AAA=2.0), 1_000.0, journal_open=[pending]
    )
    assert _kinds(other_symbol) == ["quantity"]


def test_a_pending_quantity_order_counts_at_the_buffered_reference_price() -> None:
    """3 SEC_B at a 50 reference with the 2% buffer: 153 of cash."""
    pending = _pending("tp-20261007-SEC_B-buy-1", "SEC_B", "buy", quantity=3.0)
    inside = _run(_ledger(1_000.0), _positions(BBB=3.0), 847.0, journal_open=[pending])
    assert inside.status == "pending_unresolved"
    beyond = _run(_ledger(1_000.0), _positions(BBB=3.0), 845.0, journal_open=[pending])
    assert _kinds(beyond) == ["cash"]


def test_an_own_prefix_broker_order_with_a_pending_row_is_a_resume_matter() -> None:
    pending = _pending("tp-20261007-SEC_B-buy-1", "SEC_B", "buy", notional=200.0)
    held = _reading(pending.order, OrderStatus.ACCEPTED)
    result = _run(
        _ledger(SEC_A=1.0), _positions(AAA=1.0), 1_000.0, open_orders=[held], journal_open=[pending]
    )
    assert result.status == "pending_unresolved"
    assert result.mismatches == ()


# --- precedence ------------------------------------------------------------------


def test_status_precedence_mismatch_over_pending_over_lagging_over_ok() -> None:
    pending = _pending("tp-20261007-SEC_B-buy-1", "SEC_B", "buy", notional=200.0)
    lagging = _lagging_buy(5.0, 2.0)
    both = _run(
        _ledger(1_000.0, SEC_A=12.0),
        _positions(AAA=15.0),
        700.0,
        journal_open=[pending, lagging],
    )
    assert both.status == "pending_unresolved"
    assert both.lagging_ids == (lagging.order.client_order_id,)

    with_mismatch = _run(
        _ledger(1_000.0, SEC_A=12.0),
        _positions(AAA=15.0),
        700.0,
        journal_open=[pending, lagging],
        account_id="other",
    )
    assert with_mismatch.status == "mismatch"
    assert json.loads(with_mismatch.mismatches_json)["lagging"] == [lagging.order.client_order_id]


def test_the_mismatches_json_is_stable() -> None:
    result = _run(_ledger(SEC_A=10.0), _positions(AAA=9.0), 1_000.0)
    again = _run(_ledger(SEC_A=10.0), _positions(AAA=9.0), 1_000.0)
    assert result.mismatches_json == again.mismatches_json
    assert set(json.loads(result.mismatches_json)) == {
        "mismatches",
        "lagging",
        "pending",
        "explained",
        "allowed",
        "activities",
    }


# --- review fixes: bounds, deferral, validation ---------------------------------


def test_ended_listing_proceeds_above_the_reference_value_cap_are_a_mismatch() -> None:
    """4 SEC_B at a 50 reference with the 2% buffer caps the proceeds at 204."""
    inside = _run(
        _ledger(1_000.0, SEC_B=4.0),
        {},
        1_204.0,
        explanations=_explanations(ended=frozenset({"SEC_B"})),
    )
    assert inside.status == "ok"
    beyond = _run(
        _ledger(1_000.0, SEC_B=4.0),
        {},
        1_206.0,
        explanations=_explanations(ended=frozenset({"SEC_B"})),
    )
    assert _kinds(beyond) == ["cash"]
    assert beyond.adjustments == ()


def test_an_ended_listing_during_a_lag_is_deferred_not_journaled() -> None:
    """Ended SEC_B paying 190 and a lagging SEC_A buy of 300: the cash is -110.
    Journaling proceeds now would book 0 and halt later, so nothing is proposed."""
    result = _run(
        _ledger(1_000.0, SEC_A=12.0, SEC_B=4.0),
        _positions(AAA=15.0),
        890.0,
        journal_open=[_lagging_buy(5.0, 2.0)],
        explanations=_explanations(ended=frozenset({"SEC_B"})),
    )
    assert result.status == "fills_lagging"
    assert result.adjustments == ()
    assert result.explained == ()
    reasons = {a.reason for a in result.allowed}
    assert "deferred ended listing" in reasons


def test_an_ended_listing_while_an_order_is_pending_is_deferred() -> None:
    pending = _pending("tp-20261007-SEC_A-buy-1", "SEC_A", "buy", notional=200.0)
    result = _run(
        _ledger(1_000.0, SEC_B=4.0),
        {},
        1_190.0,
        journal_open=[pending],
        explanations=_explanations(ended=frozenset({"SEC_B"})),
    )
    assert result.status == "pending_unresolved"
    assert result.adjustments == ()


def test_a_spin_off_during_a_lag_is_deferred() -> None:
    result = _run(
        _ledger(700.0, SEC_A=12.0),
        _positions(AAA=15.0, KID=3.0),
        400.0,
        journal_open=[_lagging_buy(5.0, 2.0)],
        explanations=_explanations(spinoffs={"SEC_KID": ("SEC_A", 0.25)}),
    )
    assert result.status == "fills_lagging"
    assert result.adjustments == ()
    assert [a.reason for a in result.allowed if a.security_id == "SEC_KID"] == [
        "deferred spin-off receipt"
    ]


def test_a_spin_off_must_match_its_ratio() -> None:
    """10 parent shares at 0.25 is 2.5 children: 3 is too many, 1.4 too few
    even for cash in lieu of a fraction."""
    for received in (3.0, 1.4):
        result = _run(
            _ledger(1_000.0, SEC_A=10.0),
            _positions(AAA=10.0, KID=received),
            1_000.0,
            explanations=_explanations(spinoffs={"SEC_KID": ("SEC_A", 0.25)}),
        )
        assert _kinds(result) == ["broker_only_position"], received
    in_lieu = _run(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=10.0, KID=2.0),
        1_002.0,
        explanations=_explanations(spinoffs={"SEC_KID": ("SEC_A", 0.25)}),
    )
    assert _kinds(in_lieu) == ["cash"]  # the cash in lieu is not explained here
    assert in_lieu.explained[0].quantity == 2.0


def test_the_lag_cash_allowance_is_the_unjournaled_notional() -> None:
    """50 journaled at 100, then 50 more at 102: the reading averages 101 on
    100, so 5,100 of cash is still unjournaled, not 50 x 101 = 5,050."""
    row = _order_row("tp-20261007-SEC_A-buy-1", "SEC_A", "buy", quantity=100.0)
    item = JournalOpenOrder(
        row,
        pending=False,
        journaled_quantity=50.0,
        journaled_notional=5_000.0,
        reading=_reading(row, OrderStatus.FILLED, filled=100.0, avg=101.0),
    )
    result = _run(
        _ledger(10_000.0, SEC_A=50.0), _positions(AAA=100.0), 4_900.0, journal_open=[item]
    )
    assert result.status == "fills_lagging"


def test_a_lagging_sell_allows_more_cash_and_fewer_shares() -> None:
    row = _order_row("tp-20261007-SEC_A-sell-1", "SEC_A", "sell", quantity=5.0)
    item = JournalOpenOrder(
        row,
        pending=False,
        journaled_quantity=2.0,
        journaled_notional=200.0,
        reading=_reading(row, OrderStatus.FILLED, filled=5.0, avg=100.0),
    )
    ok = _run(_ledger(1_000.0, SEC_A=8.0), _positions(AAA=5.0), 1_300.0, journal_open=[item])
    assert ok.status == "fills_lagging"
    wrong_way = _run(_ledger(1_000.0, SEC_A=8.0), _positions(AAA=11.0), 700.0, journal_open=[item])
    assert sorted(_kinds(wrong_way)) == ["cash", "quantity"]


@pytest.mark.parametrize(
    ("filled", "avg"),
    [(5.0, None), (5.0, 0.0), (5.0, float("inf")), (float("inf"), 100.0), (50.0, 100.0)],
)
def test_a_reading_that_cannot_bound_the_lag_is_a_mismatch(
    filled: float, avg: float | None
) -> None:
    row = _order_row("tp-20261007-SEC_A-buy-1", "SEC_A", "buy", quantity=5.0)
    reading = Order.__new__(Order)  # bypass validation to model a bad adapter reading
    object.__setattr__(
        reading,
        "__dict__",
        {
            **vars(_reading(row, OrderStatus.ACCEPTED)),
            "filled_quantity": filled,
            "filled_avg_price": avg,
        },
    )
    item = JournalOpenOrder(
        row, pending=False, journaled_quantity=2.0, journaled_notional=200.0, reading=reading
    )
    result = _run(
        _ledger(1_000.0, SEC_A=2.0),
        _positions(AAA=2.0),
        1_000.0,
        open_orders=[reading],
        journal_open=[item],
    )
    assert "bad_reading" in _kinds(result)


def test_pending_allowances_are_one_way_and_listed() -> None:
    pending = _pending("tp-20261007-SEC_B-buy-1", "SEC_B", "buy", notional=200.0)
    wrong_way = _run(
        _ledger(1_000.0, SEC_B=4.0), _positions(BBB=1.0), 1_150.0, journal_open=[pending]
    )
    assert sorted(_kinds(wrong_way)) == ["cash", "quantity"]
    too_many = _run(_ledger(1_000.0), _positions(BBB=4.2), 800.0, journal_open=[pending])
    assert _kinds(too_many) == ["broker_only_position"]  # 200 / 50 x 1.02 = 4.08
    inside = _run(_ledger(1_000.0), _positions(BBB=3.9), 805.0, journal_open=[pending])
    listed = json.loads(inside.mismatches_json)["allowed"]
    assert {entry["reason"] for entry in listed} == {
        "cash within the open allowances",
        "quantity within the open allowances",
    }


def test_adjustments_are_proposed_only_on_an_ok_result() -> None:
    result = _run(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=10.0),
        1_004.5,
        explanations=_explanations(dividends={"SEC_A": 4.5}),
        account_id="other",
    )
    assert result.status == "mismatch"
    assert result.adjustments == ()
    assert [a.kind for a in result.explained] == ["dividend_cash"]


def test_an_own_open_order_for_another_symbol_differs_from_its_row() -> None:
    row = _order_row("tp-20261007-SEC_A-buy-1", "SEC_A", "buy", notional=100.0)
    swapped = _reading(
        _order_row(row.client_order_id, "SEC_B", "buy", notional=100.0), OrderStatus.ACCEPTED
    )
    result = _run(
        _ledger(SEC_A=1.0),
        _positions(AAA=1.0),
        1_000.0,
        open_orders=[swapped],
        journal_open=[
            JournalOpenOrder(
                row,
                pending=False,
                journaled_quantity=0.0,
                journaled_notional=0.0,
                reading=_reading(row, OrderStatus.ACCEPTED),
            )
        ],
    )
    assert _kinds(result) == ["open_order_differs"]


@pytest.mark.parametrize(
    "shape",
    [{"order_type": "limit", "limit_price": 99.0}, {"time_in_force": "gtc"}],
    ids=["limit", "gtc"],
)
def test_an_own_open_order_of_another_shape_differs_from_its_row(shape: dict[str, object]) -> None:
    """ADR 0015 seam 3 (T135b): the journal holds the order open, for its
    symbol and side, but the broker's open order is not the market day order:
    `open_order_differs`, never reconciled; the default shape passes."""
    row = _order_row("tp-20261007-SEC_A-buy-1", "SEC_A", "buy", notional=100.0)
    reading = _reading(row, OrderStatus.ACCEPTED)

    def run(open_order: Order) -> Reconciliation:
        return _run(
            _ledger(SEC_A=1.0),
            _positions(AAA=1.0),
            1_000.0,
            open_orders=[open_order],
            journal_open=[
                JournalOpenOrder(
                    row,
                    pending=False,
                    journaled_quantity=0.0,
                    journaled_notional=0.0,
                    reading=reading,
                )
            ],
        )

    assert _kinds(run(reading)) == []
    result = run(replace(reading, **shape))  # type: ignore[arg-type]
    assert _kinds(result) == ["open_order_differs"]
    (mismatch,) = result.mismatches
    assert mismatch.detail.startswith("refused_order_shape: ")


@pytest.mark.parametrize("journaled", [0.0, 5.0], ids=["fills_lagging", "fills_journaled"])
@pytest.mark.parametrize("status", [OrderStatus.FILLED, OrderStatus.ACCEPTED])
def test_an_own_order_of_another_shape_off_the_open_list_is_a_mismatch(
    journaled: float, status: OrderStatus
) -> None:
    """#1293 (owner 2026-10-10): a journal-open order the broker no longer
    lists open, whose `get_order` reading is not the market day order (a
    limit order that filled before reconciliation), is `order_differs`,
    never reconciled; its terminal reading no longer exempts it. The default
    shape still reconciles (lagging, or `missing_open_order` when the reading
    is not terminal)."""
    item = _lagging_buy(5.0, journaled)
    item = replace(item, reading=replace(item.reading, status=status))  # type: ignore[arg-type]
    shaped = replace(
        item,
        reading=replace(item.reading, order_type="limit", limit_price=99.0),  # type: ignore[arg-type]
    )

    def run(entry: JournalOpenOrder) -> Reconciliation:
        return _run(
            _ledger(1_000.0 - journaled * 100.0, SEC_A=10.0 + journaled),
            _positions(AAA=15.0),
            500.0,
            journal_open=[entry],
        )

    expected = [] if status is OrderStatus.FILLED else ["missing_open_order"]
    assert _kinds(run(item)) == expected
    result = run(shaped)
    assert result.status == "mismatch"
    assert _kinds(result) == [*expected, "order_differs"]
    (mismatch,) = [m for m in result.mismatches if m.kind == "order_differs"]
    assert mismatch.detail.startswith("refused_order_shape: ")
    assert mismatch.client_order_id == "tp-20261007-SEC_A-buy-1"


def test_inputs_that_cannot_be_compared_raise() -> None:
    row = _order_row("tp-20261007-SEC_A-buy-1", "SEC_A", "buy", notional=100.0)
    item = JournalOpenOrder(
        row,
        pending=False,
        journaled_quantity=0.0,
        journaled_notional=0.0,
        reading=_reading(row, OrderStatus.ACCEPTED),
    )
    with pytest.raises(ValueError, match="twice"):
        _run(_ledger(), {}, 1_000.0, open_orders=[item.reading], journal_open=[item, item])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="finite"):
        _run(_ledger(float("nan")), {}, 1_000.0)
    with pytest.raises(ValueError, match="finite"):
        _run(_ledger(), {}, float("inf"))
    with pytest.raises(ValueError, match="reference price"):
        _run(_ledger(), {}, 1_000.0, explanations=_explanations(reference_prices={"SEC_A": 0.0}))
    with pytest.raises(ValueError, match="maps from"):
        _run(
            _ledger(), {}, 1_000.0, explanations=_explanations(symbols={"SEC_A": "X", "SEC_B": "X"})
        )


# --- #398, #399, #400: the notional lag bound and dividends beside other allowances


def test_an_oversized_reading_on_a_notional_order_is_a_bad_reading() -> None:
    """#398: a $500 notional buy read as 1000 shares at $100 cannot bound the lag."""
    result = _run(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=1_010.0),
        -99_000.0,
        journal_open=[_lagging_buy(1_000.0, 0.0)],
    )

    assert result.status == "mismatch"
    assert "bad_reading" in _kinds(result)
    assert result.lagging_ids == ()


def test_a_notional_reading_within_its_buffered_notional_still_lags() -> None:
    """#398: 5.1 shares at $100 on a $500 order is at 500 x (1 + buffer)."""
    result = _run(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=15.1),
        490.0,
        journal_open=[_lagging_buy(5.1, 0.0)],
    )

    assert result.status == "fills_lagging"


def test_a_notional_reading_past_its_buffered_notional_is_a_bad_reading() -> None:
    """#398: 5.13 shares at $100 is $513, past 500 x 1.02 + the cash tolerance."""
    result = _run(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=15.13),
        487.0,
        journal_open=[_lagging_buy(5.13, 0.0)],
    )

    assert "bad_reading" in _kinds(result)


def test_a_credited_dividend_while_a_buy_lags_is_allowed_and_deferred() -> None:
    """#399: the broker filled 5 (journal 2) and credited $4.50 on SEC_B."""
    result = _run(
        _ledger(1_000.0, SEC_A=12.0, SEC_B=4.0),
        _positions(AAA=15.0, BBB=4.0),
        704.5,
        journal_open=[_lagging_buy(5.0, 2.0)],
        explanations=_explanations(dividends={"SEC_B": 4.5}),
    )

    assert result.status == "fills_lagging"
    assert result.adjustments == () and result.explained == ()
    assert ("deferred dividend", 4.5, "SEC_B") in [
        (a.reason, a.difference, a.security_id) for a in result.allowed
    ]


def test_a_credited_dividend_while_an_order_is_pending_is_allowed() -> None:
    """#399: a pending $500 buy the broker never filled, plus a $4.50 credit."""
    row = _order_row("tp-20261007-SEC_A-buy-1", "SEC_A", "buy", notional=500.0)
    pending = JournalOpenOrder(
        row, pending=True, journaled_quantity=0.0, journaled_notional=0.0, reading=None
    )
    result = _run(
        _ledger(1_000.0, SEC_B=4.0),
        _positions(BBB=4.0),
        1_004.5,
        journal_open=[pending],
        explanations=_explanations(dividends={"SEC_B": 4.5}),
    )

    assert result.status == "pending_unresolved"
    assert result.adjustments == ()


def test_cash_within_the_proceeds_cap_is_all_proceeds_beside_a_dividend() -> None:
    """#400: SEC_C ended (cap 5 x 20 x 1.02 = 102) and SEC_A has $4.50 due. Paper
    is not expected to pay dividends, so $102 that the proceeds alone can
    hold is all proceeds."""
    result = _run(
        _ledger(1_000.0, SEC_A=10.0, SEC_C=5.0),
        _positions(AAA=10.0),
        1_102.0,
        explanations=_explanations(ended=frozenset({"SEC_C"}), dividends={"SEC_A": 4.5}),
    )

    assert result.status == "ok"
    found = {(a.kind, a.security_id): a.cash for a in result.adjustments}
    assert found == {("corporate_action_cash", "SEC_C"): pytest.approx(102.0)}


def test_cash_beyond_the_proceeds_cap_is_read_as_the_dividend_beside_them() -> None:
    """#400: $104.50 is more than the $102 cap: $100 of proceeds and the $4.50
    dividend, not a cash mismatch."""
    result = _run(
        _ledger(1_000.0, SEC_A=10.0, SEC_C=5.0),
        _positions(AAA=10.0),
        1_104.5,
        explanations=_explanations(ended=frozenset({"SEC_C"}), dividends={"SEC_A": 4.5}),
    )

    assert result.status == "ok"
    found = {(a.kind, a.security_id): a.cash for a in result.adjustments}
    assert found == {
        ("corporate_action_cash", "SEC_C"): pytest.approx(100.0),
        ("dividend_cash", "SEC_A"): pytest.approx(4.5),
    }
    [removal] = [a for a in result.adjustments if a.kind == "corporate_action_cash"]
    assert removal.quantity == -5.0


def test_cash_beyond_the_cap_and_the_dividend_is_still_a_mismatch() -> None:
    """#400: $108.50 is more than the $102 cap plus the $4.50 dividend and the
    cash tolerance."""
    result = _run(
        _ledger(1_000.0, SEC_A=10.0, SEC_C=5.0),
        _positions(AAA=10.0),
        1_108.5,
        explanations=_explanations(ended=frozenset({"SEC_C"}), dividends={"SEC_A": 4.5}),
    )

    assert _kinds(result) == ["cash"]
    assert result.adjustments == ()


def test_an_ended_listing_whose_cash_excludes_the_dividend_keeps_all_proceeds() -> None:
    """#400: when the dividend does not fit the cash, the proceeds take it all."""
    result = _run(
        _ledger(1_000.0, SEC_A=10.0, SEC_C=5.0),
        _positions(AAA=10.0),
        1_002.0,
        explanations=_explanations(ended=frozenset({"SEC_C"}), dividends={"SEC_A": 4.5}),
    )

    assert result.status == "ok"
    found = {(a.kind, a.security_id): a.cash for a in result.adjustments}
    assert found == {("corporate_action_cash", "SEC_C"): pytest.approx(2.0)}


def test_cash_beyond_the_lag_and_the_dividend_is_still_a_mismatch() -> None:
    """#399: the band runs from the lag's -$300 to the dividend's +$4.50; a
    dollar above that plus the cash tolerance is a mismatch."""
    result = _run(
        _ledger(1_000.0, SEC_A=12.0, SEC_B=4.0),
        _positions(AAA=15.0, BBB=4.0),
        1_006.5,
        journal_open=[_lagging_buy(5.0, 2.0)],
        explanations=_explanations(dividends={"SEC_B": 4.5}),
    )

    assert _kinds(result) == ["cash"]


def test_a_deferred_dividend_widens_the_cash_band_only_upward() -> None:
    """#399: $4.50 less cash than the lag explains is not absorbed by a dividend."""
    result = _run(
        _ledger(1_000.0, SEC_A=12.0, SEC_B=4.0),
        _positions(AAA=15.0, BBB=4.0),
        695.5,
        journal_open=[_lagging_buy(5.0, 2.0)],
        explanations=_explanations(dividends={"SEC_B": 4.5}),
    )

    assert _kinds(result) == ["cash"]


def test_a_negative_dividend_due_is_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        _run(
            _ledger(1_000.0, SEC_A=10.0),
            _positions(AAA=10.0),
            1_000.0,
            explanations=_explanations(dividends={"SEC_A": -1.0}),
        )


# --- booked `FEE` cash activities (spec 2026-10-12, item 2; T183, #1583) ---------


def _activity(
    activity_id: str,
    activity_type: str,
    day: date,
    net_amount: float | None,
    status: str = "executed",
    description: str | None = None,
) -> CashActivity:
    return CashActivity(
        activity_id=activity_id,
        activity_type=activity_type,
        date=day,
        status=status,
        net_amount=net_amount,
        symbol="AAA",
        description=description,
    )


def test_a_booked_fee_is_subtracted_before_every_other_explanation() -> None:
    """A `FEE` not inside the base is booked once as a `cash`-only `fee_cash`
    adjustment keyed by its activity id, and the reconciliation is `ok`."""
    result = _run(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=10.0),
        999.98,
        explanations=_explanations(
            activities=(_activity("act-1", "FEE", S, -0.02, description="SEC fee"),)
        ),
    )

    assert result.status == "ok"
    assert [
        (a.kind, a.cash, a.security_id, a.quantity, a.broker_activity_id)
        for a in result.adjustments
    ] == [("fee_cash", -0.02, None, None, "act-1")]
    assert result.adjustments[0].explanation == "FEE: SEC fee"
    assert [(r.activity_id, r.activity_type, r.status) for r in result.activities] == [
        ("act-1", "FEE", "executed")
    ]
    assert json.loads(result.mismatches_json)["activities"] == [
        {"activity_id": "act-1", "activity_type": "FEE", "status": "executed"}
    ]


def test_the_same_gap_with_no_activity_still_halts_as_cash() -> None:
    """A `FEE` is booked by observation, never inferred from the difference: a
    -0.02 gap with no activity read is still a `cash` mismatch."""
    result = _run(_ledger(1_000.0, SEC_A=10.0), _positions(AAA=10.0), 999.98)

    assert _kinds(result) == ["cash"]


def test_a_fee_inside_the_base_is_not_booked_again() -> None:
    """A `FEE` whose id is in a seen-set is inside the base: not booked, though
    still recorded."""
    result = _run(
        _ledger(999.98, SEC_A=10.0),
        _positions(AAA=10.0),
        999.98,
        explanations=_explanations(
            activities=(_activity("act-1", "FEE", S, -0.02),),
            seen_activity_ids=frozenset({"act-1"}),
        ),
    )

    assert result.status == "ok"
    assert result.adjustments == ()
    assert [r.activity_id for r in result.activities] == ["act-1"]


def test_a_fee_dated_on_or_after_the_fallback_start_is_booked() -> None:
    """Under the fallback (null sets), a `FEE` dated before the window's start
    is seen and one dated on it is not."""
    explanations = _explanations(
        activities=(
            _activity("old", "FEE", date(2026, 9, 30), -0.02),
            _activity("new", "FEE", S, -0.01),
        ),
        activity_seen_before=S,
    )
    result = _run(
        _ledger(999.99, SEC_A=10.0), _positions(AAA=10.0), 999.98, explanations=explanations
    )

    # only the fee dated on the start date (not seen) is booked; the older one is
    # already in starting_cash.
    assert result.status == "ok"
    assert [a.broker_activity_id for a in result.adjustments] == ["new"]


@pytest.mark.parametrize("activity_type", ["INT", "JNLC", "ACATC"])
def test_an_unknown_cash_type_is_unexplained_activity(activity_type: str) -> None:
    """A cash activity of a type the rule does not know names its type, id, date,
    amount and status, and halts the window (#1583)."""
    result = _run(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=10.0),
        1_000.0,
        explanations=_explanations(activities=(_activity("act-x", activity_type, S, -1.5),)),
    )

    assert _kinds(result) == ["unexplained_activity"]
    detail = result.mismatches[0].detail
    assert activity_type in detail and "act-x" in detail and "executed" in detail


def test_a_fee_whose_status_is_not_executed_is_unexplained_activity() -> None:
    result = _run(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=10.0),
        1_000.0,
        explanations=_explanations(activities=(_activity("act-1", "FEE", S, -0.02, "corrected"),)),
    )

    assert _kinds(result) == ["unexplained_activity"]
    assert result.adjustments == ()


def test_a_journaled_fee_the_broker_corrected_is_a_mismatch() -> None:
    """A `FEE` the base recorded whose status is no longer `executed` halts even
    though it is inside the base."""
    result = _run(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=10.0),
        1_000.0,
        explanations=_explanations(
            activities=(_activity("act-1", "FEE", S, -0.02, "canceled"),),
            seen_activity_ids=frozenset({"act-1"}),
        ),
    )

    assert _kinds(result) == ["unexplained_activity"]
    assert result.adjustments == ()


def test_an_activity_inside_the_base_is_never_unexplained() -> None:
    """#1641: an activity already inside the base is never a mismatch, whatever
    its type. The daily book's own $100,000 funding `JNLC`, dated the day before
    the window's start date under the fallback (null recorded sets), is already
    in the cash the ledger starts from, so it reconciles `ok` (recorded on the
    row, never flagged). Before the fix `_activity_mismatch` ran before
    `_inside_base` and halted on it."""
    result = _run(
        _ledger(100_000.0),
        _positions(),
        100_000.0,
        explanations=_explanations(
            activities=(
                _activity("20261009000000000::cd4b3757", "JNLC", date(2026, 10, 9), 100_000.0),
            ),
            activity_seen_before=date(2026, 10, 10),
        ),
    )

    assert result.status == "ok"
    assert _kinds(result) == []
    assert result.adjustments == ()
    assert [r.activity_id for r in result.activities] == ["20261009000000000::cd4b3757"]


def test_an_unknown_type_inside_a_seen_set_is_never_unexplained() -> None:
    """#1641: the same holds by the seen-set route, not only the fallback: an
    `INT` a base recorded is inside the base and never a mismatch, because its
    effect is already in the ledger's cash."""
    result = _run(
        _ledger(1_000.0),
        _positions(),
        1_000.0,
        explanations=_explanations(
            activities=(_activity("act-int", "INT", S, 0.01),),
            seen_activity_ids=frozenset({"act-int"}),
        ),
    )

    assert result.status == "ok"
    assert _kinds(result) == []


def test_an_unknown_type_dated_on_the_start_date_is_still_unexplained() -> None:
    """#1641: the fallback cut is strict, so an unknown-type activity dated on the
    window's New York start date (not strictly before it) is not inside the base
    and stays `unexplained_activity`."""
    result = _run(
        _ledger(1_000.0),
        _positions(),
        1_000.0,
        explanations=_explanations(
            activities=(_activity("act-on", "JNLC", date(2026, 10, 10), 25.0),),
            activity_seen_before=date(2026, 10, 10),
        ),
    )

    assert _kinds(result) == ["unexplained_activity"]
    assert "JNLC" in result.mismatches[0].detail


def test_a_nonexecuted_dividend_inside_the_base_still_halts() -> None:
    """#1641 (safety-reviewer NIT 1): the status-integrity check is base-independent
    for dividends too — a `DIV` whose `status` is not `executed` halts as
    `unexplained_activity` even though its id is inside the base, so a later edit
    that exempts in-base dividends cannot pass here."""
    result = _run(
        _ledger(1_000.0),
        _positions(),
        1_000.0,
        explanations=_explanations(
            activities=(_activity("act-div", "DIV", date(2026, 10, 9), 4.5, "canceled"),),
            activity_seen_before=date(2026, 10, 10),
        ),
    )

    assert _kinds(result) == ["unexplained_activity"]
    assert result.adjustments == ()


def test_an_in_base_deposit_without_the_cash_still_halts_as_cash() -> None:
    """#1641 (safety-reviewer NIT 2): the in-base exemption is safe only because a
    back-dated deposit is never booked and never widens the cash band. A deposit
    whose id is inside the base (fallback) but whose amount is only in broker cash
    still halts as `cash` — never `ok` — so the id can never be written into a
    recorded set and laundered into the base later."""
    result = _run(
        _ledger(1_000.0),
        _positions(),
        1_500.0,  # broker cash is ledger cash + the $500 deposit
        explanations=_explanations(
            activities=(_activity("act-dep", "JNLC", date(2026, 10, 9), 500.0),),
            activity_seen_before=date(2026, 10, 10),
        ),
    )

    assert result.status == "mismatch"
    assert _kinds(result) == ["cash"]
    assert result.broker_cash is None  # never a re-base


@pytest.mark.parametrize("activity_type", ["SSP", "MA", "REORG"])
def test_a_corporate_action_row_is_recorded_and_never_a_mismatch(activity_type: str) -> None:
    result = _run(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=10.0),
        1_000.0,
        explanations=_explanations(activities=(_activity("act-1", activity_type, S, None),)),
    )

    assert result.status == "ok"
    assert _kinds(result) == []
    assert [r.activity_type for r in result.activities] == [activity_type]


def test_a_dividend_activity_is_recorded_but_never_booked_by_id() -> None:
    """A `DIV` row is recorded and left to the store's dividend rule, never booked
    as an adjustment by its id."""
    result = _run(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=10.0),
        1_000.0,
        explanations=_explanations(activities=(_activity("act-1", "DIV", S, 4.5),)),
    )

    assert result.status == "ok"
    assert result.adjustments == ()
    assert [r.activity_type for r in result.activities] == ["DIV"]


# --- booked `CIL` cash-in-lieu activities for a held stock dividend (#1627) ----


def _cil(
    activity_id: str, day: date, net_amount: float, status: str = "executed", symbol: str = "AAA"
) -> CashActivity:
    return CashActivity(
        activity_id=activity_id,
        activity_type="CIL",
        date=day,
        status=status,
        net_amount=net_amount,
        symbol=symbol,
        description=None,
    )


# A stock dividend of ratio 1.20: the reference (pre-ex close per old share) is
# 120.00, so the post-ex price per new share is 120.00 / 1.20 = 100.00, and the
# 0.5-share remainder the ledger still shows is worth 50.00. Ratio > 1.11
# (#1627-6a).
_CIL_RATIO = 1.20
_CIL_REFERENCE = 120.0
_CIL_EX_DATE = date(2026, 10, 1)
_CIL_PAYABLE = date(2026, 10, 8)  # five XNYS sessions after the ex-date


def test_a_stock_dividend_cash_in_lieu_reconciles_with_no_halt() -> None:
    """#1627: a book holding a stock-dividend issuer over the ex-date reconciles
    with no halt. The ledger holds 100 x 1.2 = ... stated as 100.5 (a 0.5 remainder
    the split leaves); the broker credits floor(100.5) = 100 whole shares plus a
    `CIL` for the 0.5 share at the post-ex 120 / 1.2 = 100.00 = 50.00 (#1627-6a:
    the pre-ex reference is divided by the ratio, so a stock dividend above ~11%
    no longer halts). `compare` books one `cash_in_lieu` adjustment (quantity
    -0.5, cash the broker's 50.00, keyed by its id) and rounds the position to 100."""
    result = _run(
        _ledger(1_000.0, SEC_A=100.5),
        _positions(AAA=100.0),
        1_050.0,  # broker cash = ledger cash + the 50.00 paid in lieu
        explanations=_explanations(
            stock_dividends={"SEC_A": (S, S, _CIL_RATIO)},
            stock_dividend_references={"SEC_A": _CIL_REFERENCE},
            activities=(_cil("act-cil", S, 50.0),),
        ),
    )

    assert result.status == "ok"
    assert _kinds(result) == []
    assert [
        (a.kind, a.cash, a.security_id, a.quantity, a.broker_activity_id)
        for a in result.adjustments
    ] == [("cash_in_lieu", 50.0, "SEC_A", -0.5, "act-cil")]
    assert [r.activity_id for r in result.activities] == ["act-cil"]


def test_a_cash_in_lieu_within_the_relative_tolerance_books_the_broker_amount() -> None:
    """#1627: the tolerance is `risk.cash_in_lieu_tolerance` and relative, so a
    `CIL` within ~10% of the expected 50.00 still books — and it books the
    broker's exact amount by id (46.00, 8% under), not the expected."""
    result = _run(
        _ledger(1_000.0, SEC_A=100.5),
        _positions(AAA=100.0),
        1_046.00,  # the 46.00 the broker actually credited
        explanations=_explanations(
            stock_dividends={"SEC_A": (S, S, _CIL_RATIO)},
            stock_dividend_references={"SEC_A": _CIL_REFERENCE},
            activities=(_cil("act-cil", S, 46.0),),
        ),
    )

    assert result.status == "ok"
    assert [(a.kind, a.cash, a.broker_activity_id) for a in result.adjustments] == [
        ("cash_in_lieu", 46.0, "act-cil")
    ]


def test_a_cash_in_lieu_posts_within_the_lag_window_and_books() -> None:
    """#1627-6b: the whole-share credit and the `CIL` may post on different
    sessions (Alpaca pays on the payable date), so a CIL dated after the ex-date
    but within `[ex_date, last_acceptable]` (one session's lag here) books."""
    result = _run(
        _ledger(1_000.0, SEC_A=100.5),
        _positions(AAA=100.0),
        1_050.0,
        explanations=_explanations(
            stock_dividends={"SEC_A": (date(2026, 10, 6), S, _CIL_RATIO)},
            stock_dividend_references={"SEC_A": _CIL_REFERENCE},
            activities=(_cil("act-cil", S, 50.0),),  # dated one session after ex-date
        ),
    )

    assert result.status == "ok"
    assert [(a.broker_activity_id) for a in result.adjustments] == ["act-cil"]


def test_a_cash_in_lieu_beyond_the_lag_window_halts() -> None:
    """#1627-6b: a CIL dated past the window's last acceptable session (here a
    zero-lag window keeps it on the ex-date 2026-10-06) is `unexplained_activity`."""
    result = _run(
        _ledger(1_000.0, SEC_A=100.0),
        _positions(AAA=100.0),
        1_000.0,
        explanations=_explanations(
            stock_dividends={"SEC_A": (date(2026, 10, 6), date(2026, 10, 6), _CIL_RATIO)},
            stock_dividend_references={"SEC_A": _CIL_REFERENCE},
            activities=(_cil("act-cil", S, 5.0),),  # 2026-10-07, past last = 2026-10-06
        ),
    )

    assert result.status == "mismatch"
    assert _kinds(result) == ["unexplained_activity"]
    assert "no stock dividend" in result.mismatches[0].detail


def test_a_cash_in_lieu_before_the_ex_date_halts() -> None:
    """#1627: a CIL dated before the ex-date window opens is
    `unexplained_activity` (the session does not match)."""
    result = _run(
        _ledger(1_000.0, SEC_A=100.0),
        _positions(AAA=100.0),
        1_000.0,
        explanations=_explanations(
            stock_dividends={"SEC_A": (S, S, _CIL_RATIO)},
            stock_dividend_references={"SEC_A": _CIL_REFERENCE},
            activities=(_cil("act-cil", date(2026, 10, 6), 5.0),),
        ),
    )

    assert result.status == "mismatch"
    assert _kinds(result) == ["unexplained_activity"]
    assert "no stock dividend" in result.mismatches[0].detail


def test_a_stock_dividend_with_no_cash_in_lieu_halts_as_quantity() -> None:
    """#1627: the whole-share growth with no `CIL` activity is a `quantity`
    mismatch on the unbooked fractional share, so the book halts."""
    result = _run(
        _ledger(1_000.0, SEC_A=100.5),
        _positions(AAA=100.0),
        1_000.0,
        explanations=_explanations(
            stock_dividends={"SEC_A": (S, S, _CIL_RATIO)},
            stock_dividend_references={"SEC_A": _CIL_REFERENCE},
        ),
    )

    assert result.status == "mismatch"
    assert _kinds(result) == ["quantity"]


def test_a_cash_in_lieu_beyond_the_tolerance_halts() -> None:
    """#1627: a `CIL` whose amount is off the expected cash beyond the relative
    tolerance is `unexplained_activity` (nothing books it); the unbooked share
    and cash are mismatches, and the book halts."""
    result = _run(
        _ledger(1_000.0, SEC_A=100.5),
        _positions(AAA=100.0),
        1_040.0,  # a 40.00 in lieu, 20% below the expected 50.00
        explanations=_explanations(
            stock_dividends={"SEC_A": (S, S, _CIL_RATIO)},
            stock_dividend_references={"SEC_A": _CIL_REFERENCE},
            activities=(_cil("act-cil", S, 40.0),),
        ),
    )

    assert result.status == "mismatch"
    assert "unexplained_activity" in _kinds(result)
    assert result.adjustments == ()


def test_a_cash_in_lieu_with_no_reference_price_halts() -> None:
    """#1627-6c: a `CIL` whose issuer has no reference price halts; a zero-cash
    `cash_in_lieu` is never booked."""
    result = _run(
        _ledger(1_000.0, SEC_A=100.5),
        _positions(AAA=100.0),
        1_000.0,
        explanations=_explanations(
            stock_dividends={"SEC_A": (S, S, _CIL_RATIO)},
            stock_dividend_references={},  # no bar for the name
            activities=(_cil("act-cil", S, 50.0),),
        ),
    )

    assert result.status == "mismatch"
    assert "unexplained_activity" in _kinds(result)
    assert "no reference price" in result.mismatches[0].detail
    assert result.adjustments == ()


@pytest.mark.parametrize("broker_shares", [99.0, 101.0])
def test_a_cash_in_lieu_that_is_not_a_whole_share_credit_halts(broker_shares: float) -> None:
    """#1627 (safety guard): a `CIL` beside a broker quantity that is not the
    floor of the ledger's fractional expectation halts — the whole-share guard
    stops rounding from masking a lost share. 99 strands one below the floor of
    100.5; 101 is a share above it."""
    result = _run(
        _ledger(1_000.0, SEC_A=100.5),
        _positions(AAA=broker_shares),
        1_000.0 + abs(100.5 - broker_shares) * (_CIL_REFERENCE / _CIL_RATIO),
        explanations=_explanations(
            stock_dividends={"SEC_A": (S, S, _CIL_RATIO)},
            stock_dividend_references={"SEC_A": _CIL_REFERENCE},
            activities=(
                _cil("act-cil", S, abs(100.5 - broker_shares) * (_CIL_REFERENCE / _CIL_RATIO)),
            ),
        ),
    )

    assert result.status == "mismatch"
    assert "unexplained_activity" in _kinds(result)
    assert result.adjustments == ()


def test_a_cash_in_lieu_beside_a_whole_ledger_one_share_short_halts() -> None:
    """#1627 (safety guard `abs(broker_shares - whole) <= q_tol`): a ledger that is
    already whole (100, so `q_tol < removed < 1 + q_tol` still admits `removed`)
    with the broker one share short (99) plus a `CIL` must halt on the whole-share
    guard. Deleting that guard alone lets a -1.0 share "cash in lieu" book."""
    result = _run(
        _ledger(1_000.0, SEC_A=100.0),
        _positions(AAA=99.0),
        1_000.0 + (_CIL_REFERENCE / _CIL_RATIO),
        explanations=_explanations(
            stock_dividends={"SEC_A": (S, S, _CIL_RATIO)},
            stock_dividend_references={"SEC_A": _CIL_REFERENCE},
            activities=(_cil("act-cil", S, _CIL_REFERENCE / _CIL_RATIO),),
        ),
    )

    assert result.status == "mismatch"
    assert "unexplained_activity" in _kinds(result)
    assert result.adjustments == ()


def test_a_correct_cash_in_lieu_five_sessions_after_the_ex_date_reconciles() -> None:
    """#1627 (fix 2): the valuation uses the pre-ex close of the ex-date, not the
    close before the reconciliation's own session, so a `CIL` posted on the
    payable date (five sessions after the ex-date, here) values the same
    remainder and reconciles. Ratio 1.15, so the post-ex price is 120 / 1.15."""
    result = _run(
        _ledger(1_000.0, SEC_A=100.5),
        _positions(AAA=100.0),
        1_000.0 + 0.5 * (120.0 / 1.15),
        explanations=_explanations(
            stock_dividends={"SEC_A": (_CIL_EX_DATE, _CIL_PAYABLE, 1.15)},
            stock_dividend_references={"SEC_A": 120.0},
            activities=(_cil("act-cil", _CIL_PAYABLE, 0.5 * (120.0 / 1.15)),),
        ),
    )

    assert result.status == "ok"
    assert [(a.kind, a.security_id) for a in result.adjustments] == [("cash_in_lieu", "SEC_A")]


def test_an_underpaid_cash_in_lieu_five_sessions_after_the_ex_date_halts() -> None:
    """#1627 (fix 2): the same pre-ex valuation decides, so a `CIL` paid short on
    the payable date is off the expected cash and halts."""
    result = _run(
        _ledger(1_000.0, SEC_A=100.5),
        _positions(AAA=100.0),
        1_000.0 + 0.5 * (120.0 / 1.15) * 0.5,  # half the in lieu it owed
        explanations=_explanations(
            stock_dividends={"SEC_A": (_CIL_EX_DATE, _CIL_PAYABLE, 1.15)},
            stock_dividend_references={"SEC_A": 120.0},
            activities=(_cil("act-cil", _CIL_PAYABLE, 0.5 * (120.0 / 1.15) * 0.5),),
        ),
    )

    assert result.status == "mismatch"
    assert "unexplained_activity" in _kinds(result)
    assert result.adjustments == ()


def test_a_second_cash_in_lieu_on_one_name_halts_and_books_nothing() -> None:
    """#1627 (safety guard): two CIL ids on one name in one read — after the first
    rounds the expected position to whole shares, the second sees a zero
    remainder and is `unexplained_activity`; the book halts and books nothing."""
    result = _run(
        _ledger(1_000.0, SEC_A=100.5),
        _positions(AAA=100.0),
        1_050.0,
        explanations=_explanations(
            stock_dividends={"SEC_A": (S, S, _CIL_RATIO)},
            stock_dividend_references={"SEC_A": _CIL_REFERENCE},
            activities=(_cil("act-1", S, 50.0), _cil("act-2", S, 50.0)),
        ),
    )

    assert result.status == "mismatch"
    assert "unexplained_activity" in _kinds(result)
    assert result.adjustments == ()


def test_a_cash_in_lieu_already_in_the_base_books_nothing() -> None:
    """#1627 (idempotency): a `CIL` id already inside the base (booked on a prior
    `ok` reconciliation) is never booked again, even though the whole-share
    credit already posted; the ledger is whole and the cash already carries the
    in-lieu, so the re-run is `ok` with no second `cash_in_lieu`."""
    result = _run(
        _ledger(1_050.0, SEC_A=100.0),  # ledger cash already holds the in-lieu
        _positions(AAA=100.0),
        1_050.0,
        explanations=_explanations(
            stock_dividends={"SEC_A": (S, S, _CIL_RATIO)},
            stock_dividend_references={"SEC_A": _CIL_REFERENCE},
            activities=(_cil("act-cil", S, 50.0),),
            seen_activity_ids=frozenset({"act-cil"}),
        ),
    )

    assert result.status == "ok"
    assert _kinds(result) == []
    assert result.adjustments == ()


def test_a_cash_in_lieu_whose_name_has_no_stock_dividend_halts() -> None:
    """#1627 (safety guard (d)): a `CIL` whose ex-date matches a stock dividend on
    a *different* held name — SEC_A holds the credit, but the 2026-10-07 stock
    dividend is on SEC_B — stays `unexplained_activity`."""
    result = _run(
        _ledger(1_000.0, SEC_A=100.0),
        _positions(AAA=100.0),
        1_000.0,
        explanations=_explanations(
            stock_dividends={"SEC_B": (S, S, _CIL_RATIO)},
            stock_dividend_references={"SEC_A": _CIL_REFERENCE, "SEC_B": _CIL_REFERENCE},
            activities=(_cil("act-cil", S, 5.0),),  # symbol AAA -> SEC_A, no SEC_A dividend
        ),
    )

    assert result.status == "mismatch"
    assert _kinds(result) == ["unexplained_activity"]
    assert "no stock dividend" in result.mismatches[0].detail


def test_a_nonexecuted_cash_in_lieu_is_one_mismatch_not_two() -> None:
    """#1627-6d: a `CIL` whose `status` is not `executed` is classified once by
    `_activity_mismatch` (one `unexplained_activity`); `_place_cash_in_lieu`
    never adds a second for the same read."""
    result = _run(
        _ledger(1_000.0, SEC_A=100.0),
        _positions(AAA=100.0),
        1_000.0,
        explanations=_explanations(
            stock_dividends={"SEC_A": (S, S, _CIL_RATIO)},
            stock_dividend_references={"SEC_A": _CIL_REFERENCE},
            activities=(_cil("act-cil", S, 50.0, "canceled"),),
        ),
    )

    assert result.status == "mismatch"
    assert _kinds(result) == ["unexplained_activity"]


def test_a_stock_dividend_tolerance_is_the_frozen_on_not_the_default() -> None:
    """#1627-6e: the tolerance that decides is the window's frozen value, not the
    default. A 46.00 CIL (8% under the expected 50.00) books at the default 0.10
    but halts at a frozen 0.02 (46 is beyond 0.02 x 50)."""
    ok_at_default = compare(
        _ledger(1_000.0, SEC_A=100.5),
        _positions(AAA=100.0),
        (),
        (),
        _account(1_046.0),
        _explanations(
            stock_dividends={"SEC_A": (S, S, _CIL_RATIO)},
            stock_dividend_references={"SEC_A": _CIL_REFERENCE},
            activities=(_cil("act-cil", S, 46.0),),
        ),
        WINDOW,
        RiskConfig(cash_in_lieu_tolerance=0.02),
        order_id_prefix=PREFIX,
    )
    assert ok_at_default.status == "mismatch"  # 46 is 8% off, beyond the frozen 2%

    still_ok = compare(
        _ledger(1_000.0, SEC_A=100.5),
        _positions(AAA=100.0),
        (),
        (),
        _account(1_046.0),
        _explanations(
            stock_dividends={"SEC_A": (S, S, _CIL_RATIO)},
            stock_dividend_references={"SEC_A": _CIL_REFERENCE},
            activities=(_cil("act-cil", S, 46.0),),
        ),
        WINDOW,
        RiskConfig(cash_in_lieu_tolerance=0.10),
        order_id_prefix=PREFIX,
    )
    assert still_ok.status == "ok"
    assert [(a.kind, a.cash) for a in still_ok.adjustments] == [("cash_in_lieu", 46.0)]


def test_a_cash_in_lieu_beside_a_lagging_order_is_a_deferred_allowed() -> None:
    """#1627 (safety-2): when an order is lagging, a `CIL` that would be bookable
    is recorded as an `Allowed` ("deferred cash in lieu") instead of journaled,
    so the audit trail shows why the run did not halt (§1583's deferred fee); its
    cash is still subtracted and the book reconciles the filled part."""
    result = _run(
        _ledger(700.0, SEC_A=12.0, SEC_B=100.5),
        _positions(AAA=15.0, BBB=100.0),
        450.0,  # the SEC_A lagging buy leaves the broker $300 lighter; the CIL adds $50
        journal_open=[_lagging_buy(5.0, 2.0)],
        explanations=_explanations(
            stock_dividends={"SEC_B": (S, S, _CIL_RATIO)},
            stock_dividend_references={"SEC_B": _CIL_REFERENCE},
            activities=(_cil("act-cil", S, 50.0, symbol="BBB"),),
        ),
    )

    assert result.status == "fills_lagging"
    assert result.adjustments == ()  # never journaled here
    assert any(
        a.reason == "deferred cash in lieu" and a.difference == 50.0 and a.security_id == "SEC_B"
        for a in result.allowed
    )


def test_an_issuer_with_no_stock_dividend_reconciles_unchanged() -> None:
    """#1627: an issuer with no stock dividend (a plain growth, whole result) and
    no `CIL` reconciles exactly as before."""
    result = _run(_ledger(1_000.0, SEC_A=200.0), _positions(AAA=200.0), 1_000.0)

    assert result.status == "ok"
    assert result.adjustments == ()


def test_exactly_one_cent_at_six_figures_passes_in_decimal() -> None:
    """The bound is computed in `Decimal`, so a whole-cent difference at a
    six-figure cash balance compares exactly and is inclusive (#1583)."""
    assert _run(_ledger(100_008.90), _positions(), 100_008.89).status == "ok"
    assert _kinds(_run(_ledger(100_008.90), _positions(), 100_008.88)) == ["cash"]


def test_a_dividend_beside_ten_fills_of_rounding_passes_at_the_scaled_bound() -> None:
    """The bound in the dividend comparison (#1583, Q7): a 0.09 cash gap whose
    0.05 is a credited dividend leaves 0.04 of per-fill rounding, inside
    0.01 x (1 + 10/2) = 0.06; the #1290 bare 0.01 would have mismatched it."""
    result = _run(
        _ledger(1_000.0, fills_since_base=10, SEC_A=10.0),
        _positions(AAA=10.0),
        1_000.09,
        explanations=_explanations(dividends={"SEC_A": 0.05}),
    )

    assert result.status == "ok"
    assert [a.kind for a in result.adjustments] == ["dividend_cash"]


def test_an_ended_listing_beside_ten_fills_of_rounding_passes_at_the_scaled_bound() -> None:
    """The bound in the ended-listing proceeds comparison (#1583, Q9): SEC_B
    (1 share, reference $50, cap $51) is gone and its sale left $51.05, five cents
    past the cap, inside the 0.06 ten-fill bound; the base 0.01 would not."""
    result = _run(
        _ledger(1_000.0, fills_since_base=10, SEC_A=10.0, SEC_B=1.0),
        _positions(AAA=10.0),
        1_051.05,
        explanations=_explanations(ended={"SEC_B"}, reference_prices={"SEC_B": 50.0}),
    )

    assert _kinds(result) == []
    assert [a.kind for a in result.adjustments if a.kind == "corporate_action_cash"]


def test_a_cent_fee_exactly_at_the_bound_passes() -> None:
    """A booked `fee_cash` of exactly one cent at the bound reconciles (Q13)."""
    result = _run(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=10.0),
        999.99,
        explanations=_explanations(activities=(_activity("act-1", "FEE", S, -0.01),)),
    )
    assert result.status == "ok"
    assert [a.broker_activity_id for a in result.adjustments] == ["act-1"]


def test_a_repeated_activity_id_in_one_read_is_refused() -> None:
    """A broker fact is keyed by its id (S-NIT2): an id listed twice in one read is
    a broker fault, never two facts to book; `compare` refuses it, naming the id."""
    duplicated = (_activity("act-1", "FEE", S, -0.01), _activity("act-1", "FEE", S, -0.01))
    with pytest.raises(ValueError, match="'act-1' is listed twice"):
        _run(
            _ledger(1_000.0, SEC_A=10.0),
            _positions(AAA=10.0),
            1_000.0,
            explanations=_explanations(activities=duplicated),
        )

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

from tradepartner.adapters.broker import Account, Order, OrderStatus, Position, Side
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
FROZEN = RiskConfig()  # quantity tolerance 1e-6, cash tolerance 0.01
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


def _ledger(cash: float = 1_000.0, **positions: float) -> Ledger:
    return Ledger(positions=positions, cash=cash, through=S)


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
        (_ledger(SEC_A=10.0), _positions(AAA=10.0), 1_000.02, "cash"),
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
    beyond = _run(_ledger(1_000.0), _positions(BBB=3.0), 846.0, journal_open=[pending])
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
        1_205.0,
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
        1_001.0,
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
    """#398: 5.11 shares at $100 is $511, past 500 x 1.02 + the cash tolerance."""
    result = _run(
        _ledger(1_000.0, SEC_A=10.0),
        _positions(AAA=15.11),
        489.0,
        journal_open=[_lagging_buy(5.11, 0.0)],
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
    """#400: $107.50 is more than the $102 cap plus the $4.50 dividend."""
    result = _run(
        _ledger(1_000.0, SEC_A=10.0, SEC_C=5.0),
        _positions(AAA=10.0),
        1_107.5,
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
    dollar above that is a mismatch."""
    result = _run(
        _ledger(1_000.0, SEC_A=12.0, SEC_B=4.0),
        _positions(AAA=15.0, BBB=4.0),
        1_005.5,
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

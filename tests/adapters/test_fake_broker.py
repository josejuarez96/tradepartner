"""Tests for `adapters.broker` and `adapters.fake_broker` (T20).

No risk logic is exercised or expected here (ADR 0003 rule 7): the "sell
more than held" test documents that a negative position is exactly what
`FakeBroker` produces, on purpose, and that stopping it is the risk-gated
wrapper's job (Phase 4), not this adapter's.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tradepartner.adapters.broker import Fill, Order
from tradepartner.adapters.fake_broker import (
    DuplicateOrderError,
    FakeBroker,
    OrderNotCancellableError,
    OrderNotFoundError,
)

T0 = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)  # a submission instant
OPEN_1 = datetime(2026, 1, 6, 14, 30, tzinfo=UTC)  # T+1 open
CLOSE_1 = datetime(2026, 1, 6, 21, 0, tzinfo=UTC)  # T+1 close
OPEN_2 = datetime(2026, 1, 7, 14, 30, tzinfo=UTC)
CLOSE_2 = datetime(2026, 1, 7, 21, 0, tzinfo=UTC)


def make_order(
    client_order_id: str = "co-1",
    *,
    security_id: str = "sec-aaa",
    symbol: str = "AAA",
    side: str = "buy",
    qty: Decimal | int = 10,
    order_type: str = "market",
    limit_price: Decimal | int | None = None,
    submitted_at: datetime = T0,
) -> Order:
    return Order(
        client_order_id=client_order_id,
        security_id=security_id,
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        qty=Decimal(qty),
        order_type=order_type,  # type: ignore[arg-type]
        limit_price=Decimal(limit_price) if limit_price is not None else None,
        submitted_at=submitted_at,
    )


# --- submit / accept -------------------------------------------------------


def test_submit_returns_accepted_and_is_reflected_in_order_status() -> None:
    broker = FakeBroker()
    order = make_order()

    status = broker.submit(order)

    assert status == "accepted"
    assert broker.order_status(order.client_order_id) == "accepted"
    assert broker.orders[order.client_order_id].order == order


def test_duplicate_client_order_id_rejected_and_original_unchanged() -> None:
    broker = FakeBroker()
    original = make_order(qty=10)
    broker.submit(original)

    duplicate = make_order(qty=99)
    with pytest.raises(DuplicateOrderError):
        broker.submit(duplicate)

    # The original order is untouched: same object, same status.
    assert broker.orders[original.client_order_id].order == original
    assert broker.orders[original.client_order_id].order.qty == Decimal(10)
    assert broker.order_status(original.client_order_id) == "accepted"


# --- market fill at open -----------------------------------------------


def test_market_order_fills_at_session_open_price() -> None:
    broker = FakeBroker()
    order = make_order(order_type="market", side="buy", qty=10)
    broker.submit(order)

    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("101.50")})

    assert broker.order_status(order.client_order_id) == "filled"
    fills = broker.fills(since=OPEN_1)
    assert len(fills) == 1
    fill = fills[0]
    assert fill.client_order_id == order.client_order_id
    assert fill.qty == Decimal(10)
    assert fill.price == Decimal("101.50")
    assert fill.filled_at == OPEN_1


def test_market_order_for_a_security_missing_from_prices_stays_accepted() -> None:
    broker = FakeBroker()
    order = make_order(security_id="sec-bbb")
    broker.submit(order)

    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})

    assert broker.order_status(order.client_order_id) == "accepted"
    assert broker.fills(since=OPEN_1) == []


# --- limit fill only when satisfied -------------------------------------


@pytest.mark.parametrize(
    ("side", "limit_price", "open_price", "expect_fill"),
    [
        ("buy", "100.00", "99.50", True),  # open <= limit -> fills
        ("buy", "100.00", "100.00", True),  # exactly at limit -> fills
        ("buy", "100.00", "100.01", False),  # open above limit -> no fill
        ("sell", "100.00", "100.50", True),  # open >= limit -> fills
        ("sell", "100.00", "100.00", True),  # exactly at limit -> fills
        ("sell", "100.00", "99.99", False),  # open below limit -> no fill
    ],
)
def test_limit_order_fills_only_when_limit_satisfied(
    side: str, limit_price: str, open_price: str, expect_fill: bool
) -> None:
    broker = FakeBroker()
    order = make_order(side=side, order_type="limit", limit_price=Decimal(limit_price), qty=5)
    broker.submit(order)

    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal(open_price)})

    if expect_fill:
        assert broker.order_status(order.client_order_id) == "filled"
        assert broker.fills(since=OPEN_1)[0].price == Decimal(open_price)
    else:
        assert broker.order_status(order.client_order_id) == "accepted"
        assert broker.fills(since=OPEN_1) == []


# --- expiry at close ------------------------------------------------------


def test_unfilled_limit_order_expires_at_session_close() -> None:
    broker = FakeBroker()
    order = make_order(order_type="limit", side="buy", limit_price=Decimal("90"), qty=5)
    broker.submit(order)

    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})  # limit not satisfied
    assert broker.order_status(order.client_order_id) == "accepted"

    broker.mark_session_close(CLOSE_1)

    assert broker.order_status(order.client_order_id) == "expired"
    assert broker.fills(since=OPEN_1) == []


def test_filled_order_is_unaffected_by_a_later_session_close() -> None:
    broker = FakeBroker()
    order = make_order()
    broker.submit(order)
    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})

    broker.mark_session_close(CLOSE_1)

    assert broker.order_status(order.client_order_id) == "filled"


# --- cancel ----------------------------------------------------------------


def test_cancel_accepted_order() -> None:
    broker = FakeBroker()
    order = make_order()
    broker.submit(order)

    status = broker.cancel(order.client_order_id)

    assert status == "cancelled"
    assert broker.order_status(order.client_order_id) == "cancelled"


def test_cancel_filled_order_raises() -> None:
    broker = FakeBroker()
    order = make_order()
    broker.submit(order)
    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})

    with pytest.raises(OrderNotCancellableError):
        broker.cancel(order.client_order_id)

    # Status is unchanged by the failed cancel attempt.
    assert broker.order_status(order.client_order_id) == "filled"


def test_cancel_unknown_order_raises_order_not_found() -> None:
    broker = FakeBroker()

    with pytest.raises(OrderNotFoundError):
        broker.cancel("no-such-order")


# --- positions ---------------------------------------------------------


def test_positions_after_buy_then_partial_sell() -> None:
    broker = FakeBroker()
    buy = make_order("co-buy", side="buy", qty=10)
    broker.submit(buy)
    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})

    sell = make_order("co-sell", side="sell", qty=4, submitted_at=OPEN_1)
    broker.submit(sell)
    broker.mark_session_open(OPEN_2, {"sec-aaa": Decimal("110")})

    positions = broker.positions()
    assert len(positions) == 1
    position = positions[0]
    assert position.security_id == "sec-aaa"
    assert position.symbol == "AAA"
    assert position.qty == Decimal(6)
    # Partial close: avg_price is the original buy price, unchanged.
    assert position.avg_price == Decimal("100")


def test_positions_weighted_average_price_on_two_buys() -> None:
    broker = FakeBroker()
    buy1 = make_order("co-buy-1", side="buy", qty=10)
    broker.submit(buy1)
    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})

    buy2 = make_order("co-buy-2", side="buy", qty=10, submitted_at=OPEN_1)
    broker.submit(buy2)
    broker.mark_session_open(OPEN_2, {"sec-aaa": Decimal("120")})

    position = broker.positions()[0]
    assert position.qty == Decimal(20)
    assert position.avg_price == Decimal("110")  # (10*100 + 10*120) / 20


def test_flat_position_is_not_reported() -> None:
    broker = FakeBroker()
    buy = make_order("co-buy", side="buy", qty=10)
    broker.submit(buy)
    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})

    sell = make_order("co-sell", side="sell", qty=10, submitted_at=OPEN_1)
    broker.submit(sell)
    broker.mark_session_open(OPEN_2, {"sec-aaa": Decimal("105")})

    assert broker.positions() == []


# --- fills(since) filtering -------------------------------------------


def test_fills_since_filters_by_filled_at() -> None:
    broker = FakeBroker()
    order1 = make_order("co-1", side="buy", qty=5)
    broker.submit(order1)
    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})

    order2 = make_order("co-2", side="buy", qty=5, submitted_at=OPEN_1)
    broker.submit(order2)
    broker.mark_session_open(OPEN_2, {"sec-aaa": Decimal("101")})

    all_fills = broker.fills(since=OPEN_1)
    assert {fill.client_order_id for fill in all_fills} == {"co-1", "co-2"}

    only_second = broker.fills(since=OPEN_2)
    assert {fill.client_order_id for fill in only_second} == {"co-2"}


def test_fills_since_naive_datetime_raises() -> None:
    broker = FakeBroker()

    with pytest.raises(ValueError, match="tz-aware"):
        broker.fills(since=datetime(2026, 1, 6, 14, 30))  # noqa: DTZ001


# --- naive datetimes raise -------------------------------------------


def test_order_submitted_at_naive_datetime_raises() -> None:
    with pytest.raises(ValidationError):
        make_order(submitted_at=datetime(2026, 1, 5, 14, 30))  # noqa: DTZ001


def test_fill_filled_at_naive_datetime_raises() -> None:
    with pytest.raises(ValidationError):
        Fill(
            client_order_id="co-1",
            qty=Decimal(1),
            price=Decimal(1),
            filled_at=datetime(2026, 1, 5, 14, 30),  # noqa: DTZ001
        )


def test_mark_session_open_naive_timestamp_raises() -> None:
    broker = FakeBroker()

    with pytest.raises(ValueError, match="tz-aware"):
        broker.mark_session_open(datetime(2026, 1, 6, 14, 30), {})  # noqa: DTZ001


def test_mark_session_close_naive_timestamp_raises() -> None:
    broker = FakeBroker()

    with pytest.raises(ValueError, match="tz-aware"):
        broker.mark_session_close(datetime(2026, 1, 6, 21, 0))  # noqa: DTZ001


# --- order model validation --------------------------------------------


def test_limit_order_without_limit_price_raises() -> None:
    with pytest.raises(ValidationError):
        Order(
            client_order_id="co-1",
            security_id="sec-aaa",
            symbol="AAA",
            side="buy",
            qty=Decimal(1),
            order_type="limit",
            limit_price=None,
            submitted_at=T0,
        )


def test_market_order_with_limit_price_raises() -> None:
    with pytest.raises(ValidationError):
        Order(
            client_order_id="co-1",
            security_id="sec-aaa",
            symbol="AAA",
            side="buy",
            qty=Decimal(1),
            order_type="market",
            limit_price=Decimal(100),
            submitted_at=T0,
        )


# --- no risk logic: selling more than held is allowed here -------------


def test_sell_more_than_held_is_allowed_and_yields_a_negative_position() -> None:
    """`FakeBroker` contains no risk logic (ADR 0003 rule 7): it fills a
    sell order for more shares than are currently held, same as any
    other valid order, and the resulting position simply goes negative
    (short). Rejecting an oversized sell is the risk-gated wrapper's job
    (Phase 4), never this adapter's."""
    broker = FakeBroker()
    buy = make_order("co-buy", side="buy", qty=5)
    broker.submit(buy)
    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})

    oversized_sell = make_order("co-sell", side="sell", qty=20, submitted_at=OPEN_1)
    status = broker.submit(oversized_sell)
    assert status == "accepted"

    broker.mark_session_open(OPEN_2, {"sec-aaa": Decimal("100")})

    assert broker.order_status(oversized_sell.client_order_id) == "filled"
    position = broker.positions()[0]
    assert position.qty == Decimal(-15)

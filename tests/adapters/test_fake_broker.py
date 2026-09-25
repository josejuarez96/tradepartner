"""Tests for the `Broker` interface and `FakeBroker` (T20).

Covers: abstractness, idempotent `submit` on a repeated `client_order_id`,
`cancel` transitions and its error cases, fill/positions accounting, and
input validation (quantity, side, symbol, tz-aware timestamps).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tradepartner.adapters.broker import (
    Broker,
    DuplicateClientOrderIdError,
    OrderNotOpenError,
    OrderRequest,
    OrderStatus,
    Side,
    UnknownOrderError,
)
from tradepartner.adapters.fake_broker import FakeBroker

T0 = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def make_clock(start: datetime = T0) -> tuple[list[datetime], object]:
    """A tiny injectable clock: each call advances by one second and
    returns the ticks it produced, for deterministic assertions."""
    ticks: list[datetime] = []

    def clock() -> datetime:
        current = start + timedelta(seconds=len(ticks))
        ticks.append(current)
        return current

    return ticks, clock


def make_broker() -> tuple[FakeBroker, list[datetime]]:
    ticks, clock = make_clock()
    return FakeBroker(clock=clock), ticks  # type: ignore[arg-type]


def make_request(
    *,
    client_order_id: str = "co-1",
    symbol: str = "AAPL",
    side: Side = Side.BUY,
    quantity: float = 10,
    price: float = 100.0,
) -> OrderRequest:
    return OrderRequest(
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=price,
    )


# --- Abstractness -----------------------------------------------------


def test_broker_is_abstract() -> None:
    with pytest.raises(TypeError):
        Broker()  # type: ignore[abstract]


def test_fake_broker_implements_every_method() -> None:
    broker = FakeBroker(clock=lambda: T0)
    assert isinstance(broker, Broker)
    for name in ("submit", "cancel", "positions", "fills"):
        assert callable(getattr(broker, name))


# --- Idempotency --------------------------------------------------------


def test_duplicate_client_order_id_is_rejected_and_original_unchanged() -> None:
    broker, _ = make_broker()
    original = broker.submit(make_request(quantity=10, price=100.0))

    with pytest.raises(DuplicateClientOrderIdError):
        broker.submit(make_request(quantity=999, price=1.0))

    fills = broker.fills()
    assert len(fills) == 1
    assert fills[0].quantity == 10
    assert fills[0].price == 100.0
    assert original.quantity == 10
    assert original.price == 100.0


# --- Cancel ---------------------------------------------------------------


def test_cancel_unknown_id_raises() -> None:
    broker, _ = make_broker()
    with pytest.raises(UnknownOrderError):
        broker.cancel("does-not-exist")


def test_cancel_already_filled_raises() -> None:
    broker, _ = make_broker()
    broker.submit(make_request())
    with pytest.raises(OrderNotOpenError):
        broker.cancel("co-1")


def test_cancel_already_cancelled_raises() -> None:
    broker = FakeBroker(clock=lambda: T0, auto_fill=False)
    broker.submit(make_request())
    broker.cancel("co-1")
    with pytest.raises(OrderNotOpenError):
        broker.cancel("co-1")


def test_cancel_open_order_transitions_and_never_fills() -> None:
    broker = FakeBroker(clock=lambda: T0, auto_fill=False)
    broker.submit(make_request())
    cancelled = broker.cancel("co-1")
    assert cancelled.status is OrderStatus.CANCELLED
    assert broker.fills() == []


# --- Fills and positions -----------------------------------------------


def test_filled_order_produces_exactly_one_fill_with_expected_fields() -> None:
    broker, ticks = make_broker()
    broker.submit(make_request(client_order_id="co-1", quantity=10, price=100.0))

    fills = broker.fills()
    assert len(fills) == 1
    fill = fills[0]
    assert fill.client_order_id == "co-1"
    assert fill.quantity == 10
    assert fill.price == 100.0
    assert fill.filled_at == ticks[0]
    assert fill.filled_at.tzinfo is not None


def test_fills_returned_in_submission_order() -> None:
    broker, _ = make_broker()
    broker.submit(make_request(client_order_id="co-1", symbol="AAPL", price=100.0))
    broker.submit(make_request(client_order_id="co-2", symbol="MSFT", price=200.0))
    broker.submit(make_request(client_order_id="co-3", symbol="AAPL", price=101.0))

    ids = [fill.client_order_id for fill in broker.fills()]
    assert ids == ["co-1", "co-2", "co-3"]


def test_positions_aggregate_net_quantity_buy_and_sell() -> None:
    broker, _ = make_broker()
    broker.submit(make_request(client_order_id="co-1", symbol="AAPL", side=Side.BUY, quantity=10))
    broker.submit(make_request(client_order_id="co-2", symbol="AAPL", side=Side.SELL, quantity=4))

    positions = broker.positions()
    assert positions["AAPL"].quantity == 6


def test_position_netting_to_zero_is_removed() -> None:
    broker, _ = make_broker()
    broker.submit(make_request(client_order_id="co-1", symbol="AAPL", side=Side.BUY, quantity=10))
    broker.submit(make_request(client_order_id="co-2", symbol="AAPL", side=Side.SELL, quantity=10))

    assert "AAPL" not in broker.positions()


# --- Validation -----------------------------------------------------------


def test_quantity_must_be_positive() -> None:
    with pytest.raises(ValueError, match="quantity"):
        make_request(quantity=0)
    with pytest.raises(ValueError, match="quantity"):
        make_request(quantity=-5)


def test_symbol_must_be_non_empty() -> None:
    with pytest.raises(ValueError, match="symbol"):
        make_request(symbol="")


def test_naive_datetime_raises_on_fill() -> None:
    from tradepartner.adapters.broker import Fill

    with pytest.raises(ValueError, match="tz-aware"):
        Fill(
            client_order_id="co-1",
            symbol="AAPL",
            side=Side.BUY,
            quantity=1,
            price=1.0,
            filled_at=datetime(2026, 1, 5, 15, 0),  # naive  # noqa: DTZ001
        )


def test_naive_clock_raises_on_submit() -> None:
    broker = FakeBroker(clock=lambda: datetime(2026, 1, 5, 15, 0))  # noqa: DTZ001  # naive
    with pytest.raises(ValueError, match="tz-aware"):
        broker.submit(make_request())

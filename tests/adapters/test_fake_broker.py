"""Tests for the `Broker` interface and `FakeBroker` (T20).

Covers: abstractness, idempotent `submit` on a repeated `client_order_id`
(including against an open or cancelled original), `cancel`/`simulate_fill`
transitions and their error cases, fill/positions accounting (fill order,
not submission order; exact netting; short positions), state isolation
from returned collections, and input validation (quantity, price, side,
symbol, tz-aware timestamps normalized to UTC).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tradepartner.adapters.broker import (
    Broker,
    DuplicateClientOrderIdError,
    Fill,
    Order,
    OrderNotOpenError,
    OrderRequest,
    OrderStatus,
    Position,
    Side,
    UnknownOrderError,
)
from tradepartner.adapters.fake_broker import FakeBroker

T0 = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def make_clock(start: datetime = T0) -> tuple[list[datetime], Callable[[], datetime]]:
    """A tiny injectable clock: each call advances by one second and
    returns the ticks it produced, for deterministic assertions."""
    ticks: list[datetime] = []

    def clock() -> datetime:
        current = start + timedelta(seconds=len(ticks))
        ticks.append(current)
        return current

    return ticks, clock


def make_broker(*, auto_fill: bool = True) -> tuple[FakeBroker, list[datetime]]:
    ticks, clock = make_clock()
    return FakeBroker(clock=clock, auto_fill=auto_fill), ticks


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


def test_duplicate_client_order_id_against_open_original_leaves_positions_unchanged() -> None:
    broker, _ = make_broker(auto_fill=False)
    broker.submit(make_request(client_order_id="co-1", quantity=10, price=100.0))

    with pytest.raises(DuplicateClientOrderIdError):
        broker.submit(make_request(client_order_id="co-1", quantity=999, price=1.0))

    assert broker.positions() == {}
    broker.simulate_fill("co-1")
    assert broker.positions()["AAPL"].quantity == 10


def test_duplicate_client_order_id_against_cancelled_original_leaves_positions_unchanged() -> None:
    broker, _ = make_broker(auto_fill=False)
    broker.submit(make_request(client_order_id="co-1", quantity=10, price=100.0))
    broker.cancel("co-1")

    with pytest.raises(DuplicateClientOrderIdError):
        broker.submit(make_request(client_order_id="co-1", quantity=999, price=1.0))

    assert broker.positions() == {}
    assert broker.fills() == []


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
    broker, _ = make_broker(auto_fill=False)
    broker.submit(make_request())
    broker.cancel("co-1")
    with pytest.raises(OrderNotOpenError):
        broker.cancel("co-1")


def test_cancel_open_order_transitions_and_never_fills() -> None:
    broker, _ = make_broker(auto_fill=False)
    broker.submit(make_request())
    cancelled = broker.cancel("co-1")
    assert cancelled.status is OrderStatus.CANCELLED
    assert broker.fills() == []
    with pytest.raises(OrderNotOpenError):
        broker.simulate_fill("co-1")
    assert broker.fills() == []


# --- simulate_fill --------------------------------------------------------


def test_simulate_fill_on_open_order_with_auto_fill_disabled() -> None:
    broker, ticks = make_broker(auto_fill=False)
    broker.submit(make_request(quantity=10, price=100.0))
    filled = broker.simulate_fill("co-1")

    assert filled.status is OrderStatus.FILLED
    fills = broker.fills()
    assert len(fills) == 1
    assert fills[0].client_order_id == "co-1"
    assert fills[0].filled_at == ticks[-1]


def test_simulate_fill_after_cancel_raises() -> None:
    broker, _ = make_broker(auto_fill=False)
    broker.submit(make_request())
    broker.cancel("co-1")
    with pytest.raises(OrderNotOpenError):
        broker.simulate_fill("co-1")


def test_simulate_fill_unknown_id_raises() -> None:
    broker, _ = make_broker(auto_fill=False)
    with pytest.raises(UnknownOrderError):
        broker.simulate_fill("does-not-exist")


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


def test_fills_returned_in_fill_order() -> None:
    broker, _ = make_broker()
    broker.submit(make_request(client_order_id="co-1", symbol="AAPL", price=100.0))
    broker.submit(make_request(client_order_id="co-2", symbol="MSFT", price=200.0))
    broker.submit(make_request(client_order_id="co-3", symbol="AAPL", price=101.0))

    ids = [fill.client_order_id for fill in broker.fills()]
    assert ids == ["co-1", "co-2", "co-3"]


def test_fills_returned_in_fill_order_not_submission_order() -> None:
    broker, _ = make_broker(auto_fill=False)
    broker.submit(make_request(client_order_id="co-1", symbol="AAPL", price=100.0))
    broker.submit(make_request(client_order_id="co-2", symbol="MSFT", price=200.0))

    broker.simulate_fill("co-2")
    broker.simulate_fill("co-1")

    ids = [fill.client_order_id for fill in broker.fills()]
    assert ids == ["co-2", "co-1"]


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


def test_position_netting_uses_exact_decimal_arithmetic() -> None:
    """`0.1 + 0.2 - 0.3` is ~5.5e-17 in `float` arithmetic; netted with
    `Decimal(repr(...))` internally it must come out to exactly zero and
    the symbol must be absent, not left behind as phantom dust."""
    broker, _ = make_broker()
    broker.submit(make_request(client_order_id="co-1", symbol="AAPL", side=Side.BUY, quantity=0.1))
    broker.submit(make_request(client_order_id="co-2", symbol="AAPL", side=Side.BUY, quantity=0.2))
    broker.submit(make_request(client_order_id="co-3", symbol="AAPL", side=Side.SELL, quantity=0.3))

    assert 0.1 + 0.2 - 0.3 != 0  # sanity: float arithmetic alone would leave dust
    assert "AAPL" not in broker.positions()


def test_sell_with_no_prior_long_books_a_short_position() -> None:
    broker, _ = make_broker()
    broker.submit(make_request(client_order_id="co-1", symbol="AAPL", side=Side.SELL, quantity=5))

    assert broker.positions()["AAPL"].quantity == -5


def test_positions_sign_is_correct_for_each_side() -> None:
    broker, _ = make_broker()
    broker.submit(make_request(client_order_id="co-1", symbol="AAPL", side=Side.BUY, quantity=3))
    assert broker.positions()["AAPL"].quantity == 3
    broker.submit(make_request(client_order_id="co-2", symbol="AAPL", side=Side.SELL, quantity=1))
    assert broker.positions()["AAPL"].quantity == 2


# --- State isolation from returned collections -----------------------------


def test_mutating_returned_fills_list_does_not_affect_broker_state() -> None:
    broker, _ = make_broker()
    broker.submit(make_request(client_order_id="co-1"))

    fills = broker.fills()
    fills.clear()
    fills.append("garbage")  # type: ignore[arg-type]

    assert len(broker.fills()) == 1
    assert broker.fills()[0].client_order_id == "co-1"


def test_mutating_returned_positions_dict_does_not_affect_broker_state() -> None:
    broker, _ = make_broker()
    broker.submit(make_request(client_order_id="co-1", symbol="AAPL", quantity=10))

    positions = broker.positions()
    positions["AAPL"] = Position(symbol="AAPL", quantity=9999)
    del positions["AAPL"]

    assert broker.positions()["AAPL"].quantity == 10


# --- Validation: quantity / price -----------------------------------------


@pytest.mark.parametrize("bad_quantity", [0, -5, float("nan"), float("inf"), float("-inf")])
def test_quantity_must_be_positive_finite_on_order_request(bad_quantity: float) -> None:
    with pytest.raises(ValueError, match="quantity"):
        make_request(quantity=bad_quantity)


@pytest.mark.parametrize("bad_price", [0, -1.0, float("nan"), float("inf"), float("-inf")])
def test_price_must_be_positive_finite_on_order_request(bad_price: float) -> None:
    with pytest.raises(ValueError, match="price"):
        make_request(price=bad_price)


def test_quantity_rejects_bool() -> None:
    with pytest.raises(ValueError, match="quantity"):
        make_request(quantity=True)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_quantity", [0, -5, float("nan"), float("inf")])
def test_quantity_must_be_positive_finite_on_fill(bad_quantity: float) -> None:
    with pytest.raises(ValueError, match="quantity"):
        Fill(
            client_order_id="co-1",
            symbol="AAPL",
            side=Side.BUY,
            quantity=bad_quantity,
            price=1.0,
            filled_at=T0,
        )


@pytest.mark.parametrize("bad_price", [0, -1.0, float("nan"), float("inf")])
def test_price_must_be_positive_finite_on_fill(bad_price: float) -> None:
    with pytest.raises(ValueError, match="price"):
        Fill(
            client_order_id="co-1",
            symbol="AAPL",
            side=Side.BUY,
            quantity=1,
            price=bad_price,
            filled_at=T0,
        )


@pytest.mark.parametrize("bad_quantity", [0, -5, float("nan"), float("inf")])
def test_quantity_must_be_positive_finite_on_order(bad_quantity: float) -> None:
    with pytest.raises(ValueError, match="quantity"):
        Order(
            client_order_id="co-1",
            symbol="AAPL",
            side=Side.BUY,
            quantity=bad_quantity,
            price=1.0,
            status=OrderStatus.OPEN,
            submitted_at=T0,
        )


@pytest.mark.parametrize("bad_price", [0, -1.0, float("nan"), float("inf")])
def test_price_must_be_positive_finite_on_order(bad_price: float) -> None:
    with pytest.raises(ValueError, match="price"):
        Order(
            client_order_id="co-1",
            symbol="AAPL",
            side=Side.BUY,
            quantity=1,
            price=bad_price,
            status=OrderStatus.OPEN,
            submitted_at=T0,
        )


# --- Validation: identifiers and side --------------------------------------


def test_symbol_must_be_non_empty() -> None:
    with pytest.raises(ValueError, match="symbol"):
        make_request(symbol="")


def test_symbol_must_not_have_surrounding_whitespace() -> None:
    with pytest.raises(ValueError, match="symbol"):
        make_request(symbol=" AAPL")


def test_client_order_id_must_not_have_surrounding_whitespace() -> None:
    with pytest.raises(ValueError, match="client_order_id"):
        make_request(client_order_id="co-1 ")


def test_side_string_is_coerced_and_nets_correctly() -> None:
    broker, _ = make_broker()
    request = OrderRequest(
        client_order_id="co-1",
        symbol="AAPL",
        side="buy",  # type: ignore[arg-type]
        quantity=10,
        price=100.0,
    )
    assert request.side is Side.BUY

    broker.submit(request)
    assert broker.positions()["AAPL"].quantity == 10


def test_invalid_side_string_is_rejected() -> None:
    with pytest.raises(ValueError, match="side"):
        OrderRequest(
            client_order_id="co-1",
            symbol="AAPL",
            side="hold",  # type: ignore[arg-type]
            quantity=10,
            price=100.0,
        )


# --- Validation: timestamps -------------------------------------------------


def test_naive_datetime_raises_on_fill() -> None:
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


def test_naive_clock_raises_on_simulate_fill_path() -> None:
    broker = FakeBroker(
        clock=lambda: datetime(2026, 1, 5, 15, 0),  # noqa: DTZ001  # naive
        auto_fill=False,
    )
    # submit itself constructs an Order with the naive submitted_at, so it
    # raises before an order is ever recorded as open.
    with pytest.raises(ValueError, match="tz-aware"):
        broker.submit(make_request())


def test_non_utc_clock_is_normalized_to_utc() -> None:
    eastern = T0.astimezone(ZoneInfo("America/New_York"))

    def clock() -> datetime:
        return eastern

    broker = FakeBroker(clock=clock)
    order = broker.submit(make_request())

    assert order.submitted_at == T0
    assert order.submitted_at.tzinfo is UTC
    assert broker.fills()[0].filled_at.tzinfo is UTC

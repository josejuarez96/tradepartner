"""Tests for `FakeBroker` and the broker value objects' validation (T20,
moved to the Phase 4 types in T46b).

Covers: abstractness, idempotent `submit` on a repeated `client_order_id`
(including against an accepted or cancelled original), `cancel` (a request
returning `None`, its outcome read back through `get_order`),
`simulate_fill` transitions and their error cases, the injected price
function, `get_order`, `open_orders`, `fills(since)` inclusive at equal
timestamps, `account` and `assets` shapes, fill/positions accounting (fill
order, not submission order; exact netting; short positions), state
isolation from returned collections, input validation (quantity, price,
side, symbol, tz-aware timestamps normalized to UTC), symbol case
canonicalization so `aapl` and `AAPL` net as one position (issue #38), an
aware timestamp that overflows once converted to UTC (issue #43) raising
`ValueError` (not `OverflowError`) from `Order`/`Fill` construction, and a
bad clock (naive, overflowing, not a `datetime`, or raising) making
`submit`/`simulate_fill`/`account` raise `ClockError` with no state changed
(ADR 0007 point 4, T46).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tradepartner.adapters.broker import (
    TERMINAL_STATUSES,
    Account,
    Asset,
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
from tradepartner.errors import ClockError, SystemFaultError

T0 = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
PRICES = {"AAPL": 100.0, "MSFT": 200.0, "BRK.B": 400.0}


def price_of(symbol: str) -> float:
    return PRICES[symbol]


def make_clock(start: datetime = T0) -> tuple[list[datetime], Callable[[], datetime]]:
    """A tiny injectable clock: each call advances by one second and
    returns the ticks it produced, for deterministic assertions."""
    ticks: list[datetime] = []

    def clock() -> datetime:
        current = start + timedelta(seconds=len(ticks))
        ticks.append(current)
        return current

    return ticks, clock


def make_broker(*, auto_fill: bool = True, **kwargs: Any) -> tuple[FakeBroker, list[datetime]]:
    ticks, clock = make_clock()
    return FakeBroker(clock=clock, price_of=price_of, auto_fill=auto_fill, **kwargs), ticks


def make_request(
    *,
    client_order_id: str = "co-1",
    symbol: str = "AAPL",
    side: Side = Side.BUY,
    quantity: float | None = 10,
    notional: float | None = None,
) -> OrderRequest:
    return OrderRequest(
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,
        notional=notional,
        quantity=None if notional is not None else quantity,
    )


def make_order(**overrides: Any) -> Order:
    values: dict[str, Any] = {
        "client_order_id": "co-1",
        "symbol": "AAPL",
        "side": Side.BUY,
        "notional": None,
        "quantity": 1,
        "status": OrderStatus.ACCEPTED,
        "submitted_at": T0,
    }
    values.update(overrides)
    return Order(**values)


def make_fill(**overrides: Any) -> Fill:
    values: dict[str, Any] = {
        "client_order_id": "co-1",
        "symbol": "AAPL",
        "side": Side.BUY,
        "quantity": 1,
        "price": 1.0,
        "filled_at": T0,
        "broker_fill_id": "f-1",
    }
    values.update(overrides)
    return Fill(**values)


# --- Abstractness -----------------------------------------------------


def test_broker_is_abstract() -> None:
    with pytest.raises(TypeError):
        Broker()  # type: ignore[abstract]


def test_fake_broker_implements_every_method() -> None:
    broker, _ = make_broker()
    assert isinstance(broker, Broker)
    for name in Broker.__abstractmethods__:
        assert callable(getattr(broker, name))


def test_fake_broker_exposes_its_clock_for_the_identity_test() -> None:
    _, clock = make_clock()
    assert FakeBroker(clock=clock, price_of=price_of).clock is clock


# --- Idempotency --------------------------------------------------------


def test_duplicate_client_order_id_is_rejected_and_original_unchanged() -> None:
    broker, _ = make_broker()
    original = broker.submit(make_request(quantity=10))

    with pytest.raises(DuplicateClientOrderIdError):
        broker.submit(make_request(quantity=999))

    fills = broker.fills()
    assert len(fills) == 1
    assert fills[0].quantity == 10
    assert fills[0].price == 100.0
    assert broker.get_order("co-1") == original
    assert original.quantity == 10


def test_duplicate_client_order_id_against_accepted_original_leaves_positions_unchanged() -> None:
    broker, _ = make_broker(auto_fill=False)
    broker.submit(make_request(client_order_id="co-1", quantity=10))

    with pytest.raises(DuplicateClientOrderIdError):
        broker.submit(make_request(client_order_id="co-1", quantity=999))

    assert broker.positions() == {}
    broker.simulate_fill("co-1")
    assert broker.positions()["AAPL"].quantity == 10


def test_duplicate_client_order_id_against_cancelled_original_leaves_positions_unchanged() -> None:
    broker, _ = make_broker(auto_fill=False)
    broker.submit(make_request(client_order_id="co-1", quantity=10))
    broker.cancel("co-1")

    with pytest.raises(DuplicateClientOrderIdError):
        broker.submit(make_request(client_order_id="co-1", quantity=999))

    assert broker.positions() == {}
    assert broker.fills() == []


# --- submit -----------------------------------------------------------------


def test_submit_without_auto_fill_returns_an_accepted_order_with_a_broker_id() -> None:
    broker, ticks = make_broker(auto_fill=False)
    order = broker.submit(make_request(quantity=10))
    assert order.status is OrderStatus.ACCEPTED
    assert order.submitted_at == ticks[0]
    assert order.broker_order_id
    assert (order.filled_quantity, order.filled_avg_price, order.filled_at) == (None, None, None)


def test_auto_fill_fills_at_the_injected_price() -> None:
    broker, ticks = make_broker()
    order = broker.submit(make_request(symbol="MSFT", quantity=3))
    assert order.status is OrderStatus.FILLED
    assert (order.filled_quantity, order.filled_avg_price, order.filled_at) == (
        3.0,
        200.0,
        ticks[0],
    )
    [fill] = broker.fills()
    assert (fill.price, fill.quantity, fill.filled_at) == (200.0, 3.0, ticks[0])


def test_a_notional_order_fills_notional_over_price_shares() -> None:
    broker, _ = make_broker()
    order = broker.submit(make_request(symbol="AAPL", notional=250.0))
    assert (order.notional, order.quantity) == (250.0, None)
    assert order.filled_quantity == 2.5
    assert broker.positions()["AAPL"].quantity == 2.5


def test_broker_order_and_fill_ids_are_unique() -> None:
    broker, _ = make_broker()
    orders = [broker.submit(make_request(client_order_id=f"co-{n}")) for n in range(3)]
    assert len({o.broker_order_id for o in orders}) == 3
    assert len({f.broker_fill_id for f in broker.fills()}) == 3


def test_a_bad_price_fails_submit_before_anything_is_recorded() -> None:
    prices = {"AAPL": float("nan")}
    broker = FakeBroker(clock=make_clock()[1], price_of=prices.__getitem__, cash=500.0)
    with pytest.raises(ValueError, match="price"):
        broker.submit(make_request())
    with pytest.raises(UnknownOrderError):
        broker.get_order("co-1")
    assert broker.fills() == [] and broker.positions() == {}
    assert broker.account().cash == 500.0
    prices["AAPL"] = 10.0
    assert broker.submit(make_request(quantity=1)).broker_order_id == "fake-order-1"


def test_account_refuses_a_bad_mark_price() -> None:
    prices = {"AAPL": 10.0}
    broker = FakeBroker(clock=make_clock()[1], price_of=prices.__getitem__)
    broker.submit(make_request(quantity=1))
    prices["AAPL"] = True  # type: ignore[assignment]
    with pytest.raises(ValueError, match="price"):
        broker.account()


def test_assets_values_must_be_assets() -> None:
    with pytest.raises(TypeError, match="Asset"):
        FakeBroker(clock=make_clock()[1], price_of=price_of, assets={"X": "not-an-asset"})  # type: ignore[dict-item]


# --- get_order and open_orders ------------------------------------------------


def test_get_order_of_an_unknown_id_raises() -> None:
    broker, _ = make_broker()
    with pytest.raises(UnknownOrderError):
        broker.get_order("does-not-exist")


def test_get_order_reads_the_current_state() -> None:
    broker, _ = make_broker(auto_fill=False)
    submitted = broker.submit(make_request())
    assert broker.get_order("co-1") == submitted
    filled = broker.simulate_fill("co-1")
    assert broker.get_order("co-1") == filled


def test_open_orders_lists_every_non_terminal_order_in_submission_order() -> None:
    broker, _ = make_broker(auto_fill=False)
    for n in range(4):
        broker.submit(make_request(client_order_id=f"co-{n}"))
    broker.simulate_fill("co-1")
    broker.cancel("co-2")
    open_ids = [o.client_order_id for o in broker.open_orders()]
    assert open_ids == ["co-0", "co-3"]
    assert all(o.status not in TERMINAL_STATUSES for o in broker.open_orders())


def test_mutating_returned_open_orders_does_not_affect_broker_state() -> None:
    broker, _ = make_broker(auto_fill=False)
    broker.submit(make_request())
    broker.open_orders().clear()
    assert len(broker.open_orders()) == 1


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


def test_cancel_returns_none_and_the_outcome_is_read_back_through_get_order() -> None:
    broker, _ = make_broker(auto_fill=False)
    submitted = broker.submit(make_request())
    result = broker.cancel("co-1")  # type: ignore[func-returns-value]
    assert result is None
    cancelled = broker.get_order("co-1")
    assert cancelled.status is OrderStatus.CANCELLED
    assert cancelled.broker_order_id == submitted.broker_order_id
    assert broker.fills() == []
    with pytest.raises(OrderNotOpenError):
        broker.simulate_fill("co-1")
    assert broker.fills() == []


# --- simulate_fill --------------------------------------------------------


def test_simulate_fill_on_accepted_order_with_auto_fill_disabled() -> None:
    broker, ticks = make_broker(auto_fill=False)
    broker.submit(make_request(quantity=10))
    filled = broker.simulate_fill("co-1")

    assert filled.status is OrderStatus.FILLED
    assert filled.filled_at == ticks[-1]
    fills = broker.fills()
    assert len(fills) == 1
    assert fills[0].client_order_id == "co-1"
    assert fills[0].filled_at == ticks[-1]


def test_simulate_fill_uses_the_price_at_fill_time() -> None:
    prices = {"AAPL": 100.0}
    broker = FakeBroker(clock=make_clock()[1], price_of=prices.__getitem__, auto_fill=False)
    broker.submit(make_request())
    prices["AAPL"] = 105.0
    assert broker.simulate_fill("co-1").filled_avg_price == 105.0


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
    broker.submit(make_request(client_order_id="co-1", quantity=10))

    fills = broker.fills()
    assert len(fills) == 1
    fill = fills[0]
    assert fill.client_order_id == "co-1"
    assert fill.quantity == 10
    assert fill.price == 100.0
    assert fill.filled_at == ticks[0]
    assert fill.filled_at.tzinfo is not None
    assert fill.broker_fill_id


def test_fills_returned_in_fill_order() -> None:
    broker, _ = make_broker()
    broker.submit(make_request(client_order_id="co-1", symbol="AAPL"))
    broker.submit(make_request(client_order_id="co-2", symbol="MSFT"))
    broker.submit(make_request(client_order_id="co-3", symbol="AAPL"))

    ids = [fill.client_order_id for fill in broker.fills()]
    assert ids == ["co-1", "co-2", "co-3"]


def test_fills_returned_in_fill_order_not_submission_order() -> None:
    broker, _ = make_broker(auto_fill=False)
    broker.submit(make_request(client_order_id="co-1", symbol="AAPL"))
    broker.submit(make_request(client_order_id="co-2", symbol="MSFT"))

    broker.simulate_fill("co-2")
    broker.simulate_fill("co-1")

    ids = [fill.client_order_id for fill in broker.fills()]
    assert ids == ["co-2", "co-1"]


def test_fills_since_is_inclusive_at_equal_timestamps() -> None:
    broker, ticks = make_broker()
    for n in range(3):
        broker.submit(make_request(client_order_id=f"co-{n}"))
    assert [f.client_order_id for f in broker.fills(since=ticks[1])] == ["co-1", "co-2"]
    assert [f.client_order_id for f in broker.fills(since=ticks[2])] == ["co-2"]
    assert broker.fills(since=ticks[2] + timedelta(microseconds=1)) == []
    assert len(broker.fills(since=None)) == 3


def test_fills_since_compares_instants_across_time_zones() -> None:
    broker, ticks = make_broker()
    broker.submit(make_request())
    eastern = ticks[0].astimezone(ZoneInfo("America/New_York"))
    assert len(broker.fills(since=eastern)) == 1


def test_fills_since_must_be_tz_aware() -> None:
    broker, _ = make_broker()
    with pytest.raises(ValueError, match="since"):
        broker.fills(since=datetime(2026, 1, 5))  # noqa: DTZ001


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


# --- account and assets ------------------------------------------------------------


def test_account_shape_and_cash_follows_fills() -> None:
    broker, ticks = make_broker(cash=10_000.0, account_id="paper-1")
    before = broker.account()
    assert isinstance(before, Account)
    assert (before.account_id, before.cash, before.buying_power, before.equity) == (
        "paper-1",
        10_000.0,
        10_000.0,
        10_000.0,
    )
    assert before.as_of == ticks[-1]

    broker.submit(make_request(symbol="AAPL", quantity=10))  # 1,000 at 100
    broker.submit(make_request(client_order_id="co-2", symbol="MSFT", side=Side.SELL, quantity=1))
    after = broker.account()
    assert after.cash == 10_000.0 - 1_000.0 + 200.0
    assert after.buying_power == after.cash
    # Equity marks positions at the injected price: 10 AAPL at 100, -1 MSFT at 200.
    assert after.equity == after.cash + 1_000.0 - 200.0


def test_account_cash_is_exact_in_cents() -> None:
    broker = FakeBroker(
        clock=make_clock()[1], price_of=lambda symbol: 0.1, cash=0.3, auto_fill=True
    )
    broker.submit(make_request(quantity=1))
    broker.submit(make_request(client_order_id="co-2", quantity=2))
    assert broker.account().cash == 0.0


def test_account_reads_the_clock_through_the_wrap() -> None:
    broker = FakeBroker(clock=lambda: datetime(2026, 1, 5), price_of=price_of)  # noqa: DTZ001
    with pytest.raises(ClockError):
        broker.account()


def test_assets_default_to_tradable_fractionable_active() -> None:
    broker, _ = make_broker()
    assets = broker.assets(["aapl", "MSFT"])
    assert set(assets) == {"AAPL", "MSFT"}
    assert assets["AAPL"] == Asset(tradable=True, fractionable=True, status="active", cusip=None)


def test_assets_serve_the_configured_flags() -> None:
    halted = Asset(tradable=False, fractionable=False, status="inactive", cusip="000000000")
    broker, _ = make_broker(assets={"brk.b": halted})
    assets = broker.assets(["BRK.B", "AAPL"])
    assert assets["BRK.B"] == halted
    assert assets["AAPL"].tradable is True


def test_assets_of_no_symbols_is_empty() -> None:
    broker, _ = make_broker()
    assert broker.assets([]) == {}


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


def test_quantity_rejects_bool() -> None:
    with pytest.raises(ValueError, match="quantity"):
        make_request(quantity=True)


def test_quantity_huge_int_raises_value_error_not_overflow_error() -> None:
    # math.isfinite raises OverflowError (not a bool) on an int too large to
    # convert to float; that must surface as the same ValueError as any
    # other invalid quantity, not leak as an unhandled OverflowError.
    with pytest.raises(ValueError, match="quantity"):
        make_request(quantity=10**400)


@pytest.mark.parametrize("bad_quantity", [0, -5, float("nan"), float("inf")])
def test_quantity_must_be_positive_finite_on_fill(bad_quantity: float) -> None:
    with pytest.raises(ValueError, match="quantity"):
        make_fill(quantity=bad_quantity)


@pytest.mark.parametrize("bad_price", [0, -1.0, float("nan"), float("inf")])
def test_price_must_be_positive_finite_on_fill(bad_price: float) -> None:
    with pytest.raises(ValueError, match="price"):
        make_fill(price=bad_price)


@pytest.mark.parametrize("bad_quantity", [0, -5, float("nan"), float("inf")])
def test_quantity_must_be_positive_finite_on_order(bad_quantity: float) -> None:
    with pytest.raises(ValueError, match="quantity"):
        make_order(quantity=bad_quantity)


# --- Validation: identifiers and side --------------------------------------


def test_symbol_must_be_non_empty() -> None:
    with pytest.raises(ValueError, match="symbol"):
        make_request(symbol="")


def test_symbol_must_not_have_surrounding_whitespace() -> None:
    with pytest.raises(ValueError, match="symbol"):
        make_request(symbol=" AAPL")


# --- Symbol canonicalization (issue #38) -----------------------------------


@pytest.mark.parametrize("raw", ["aapl", "Aapl", "AAPL"])
def test_symbol_is_canonicalized_to_upper_case_on_every_value_object(raw: str) -> None:
    assert make_request(symbol=raw).symbol == "AAPL"
    assert Position(symbol=raw, quantity=1).symbol == "AAPL"
    assert make_order(symbol=raw).symbol == "AAPL"
    assert make_fill(symbol=raw).symbol == "AAPL"


def test_symbols_differing_only_in_case_net_into_one_position() -> None:
    broker, _ = make_broker()
    broker.submit(make_request(client_order_id="co-1", symbol="aapl", side=Side.BUY, quantity=10))
    broker.submit(make_request(client_order_id="co-2", symbol="AAPL", side=Side.SELL, quantity=4))

    positions = broker.positions()
    assert list(positions) == ["AAPL"]
    assert positions["AAPL"] == Position(symbol="AAPL", quantity=6)
    assert {fill.symbol for fill in broker.fills()} == {"AAPL"}


def test_symbols_differing_only_in_case_net_to_zero_and_disappear() -> None:
    broker, _ = make_broker()
    broker.submit(make_request(client_order_id="co-1", symbol="msft", side=Side.BUY, quantity=5))
    broker.submit(make_request(client_order_id="co-2", symbol="MSFT", side=Side.SELL, quantity=5))

    assert broker.positions() == {}


def test_share_class_separator_is_kept_when_canonicalizing() -> None:
    assert make_request(symbol="brk.b").symbol == "BRK.B"


@pytest.mark.parametrize("raw", ["ÄAPL", "straße", "\uff21\uff21\uff30\uff2c"])
def test_non_ascii_symbol_is_rejected(raw: str) -> None:
    """Upper-casing is only a well-defined canonical form for ASCII: `"ß".upper()`
    is `"SS"` and full-width letters look like ASCII but compare unequal."""
    with pytest.raises(ValueError, match="symbol"):
        make_request(symbol=raw)


def test_client_order_id_must_not_have_surrounding_whitespace() -> None:
    with pytest.raises(ValueError, match="client_order_id"):
        make_request(client_order_id="co-1 ")


def test_client_order_id_non_str_raises_value_error_not_attribute_error() -> None:
    # A non-str id (e.g. a plain int) used to reach `.strip()` and raise
    # AttributeError instead of the ValueError every other invalid field
    # raises.
    with pytest.raises(ValueError, match="client_order_id"):
        make_request(client_order_id=123)  # type: ignore[arg-type]


def test_side_string_is_coerced_and_nets_correctly() -> None:
    broker, _ = make_broker()
    request = OrderRequest(
        client_order_id="co-1",
        symbol="AAPL",
        side="buy",  # type: ignore[arg-type]
        quantity=10,
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
        )


# --- Validation: timestamps -------------------------------------------------


def test_naive_datetime_raises_on_fill() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        make_fill(filled_at=datetime(2026, 1, 5, 15, 0))  # naive  # noqa: DTZ001


# An aware datetime whose UTC-converted value overflows `datetime`'s
# representable range (issue #43): near `datetime.min` with a positive
# offset, and near `datetime.max` with a negative offset.
_OVERFLOWING_DATETIMES = [
    datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=5))),
    datetime.max.replace(tzinfo=timezone(timedelta(hours=-5))),
]


@pytest.mark.parametrize("overflowing", _OVERFLOWING_DATETIMES)
def test_utc_overflow_raises_value_error_on_order(overflowing: datetime) -> None:
    with pytest.raises(ValueError, match=r"submitted_at=.*out of the range") as excinfo:
        make_order(submitted_at=overflowing)
    assert isinstance(excinfo.value.__cause__, OverflowError)


@pytest.mark.parametrize("overflowing", _OVERFLOWING_DATETIMES)
def test_utc_overflow_raises_value_error_on_fill(overflowing: datetime) -> None:
    with pytest.raises(ValueError, match=r"filled_at=.*out of the range") as excinfo:
        make_fill(filled_at=overflowing)
    assert isinstance(excinfo.value.__cause__, OverflowError)


# --- Clock faults (ADR 0007 point 4, plan T46) ---------------------------------
#
# Every clock reading is validated where it is read; any failure of the call
# or the validation is a `ClockError` (a `SystemFaultError`, never a
# `ValueError`), raised before any `Order` or `Fill` is built and before any
# state changes.


def _raising_clock() -> datetime:
    raise RuntimeError("clock source unavailable")


class _DatetimeLookalike:
    """Not a `datetime`, but has every attribute `ensure_tz_aware_utc` reads."""

    tzinfo = UTC

    def utcoffset(self) -> timedelta:
        return timedelta(0)

    def astimezone(self, tz: object) -> _DatetimeLookalike:
        return self


#: (id, a bad clock reading or a callable that raises, the original error type)
_BAD_CLOCKS: list[tuple[str, object, type[BaseException]]] = [
    ("naive", datetime(2026, 1, 5, 15, 0), ValueError),  # noqa: DTZ001
    ("overflow-min", _OVERFLOWING_DATETIMES[0], ValueError),
    ("overflow-max", _OVERFLOWING_DATETIMES[1], ValueError),
    ("not-a-datetime-str", "2026-01-05T15:00:00+00:00", TypeError),
    ("not-a-datetime-none", None, TypeError),
    ("not-a-datetime-lookalike", _DatetimeLookalike(), TypeError),
    ("raises", _raising_clock, RuntimeError),
]


class _SwitchableClock:
    """Returns `T0` (advancing a second per call) while `good`, else the bad
    reading, or calls the bad callable."""

    def __init__(self, bad: object) -> None:
        self.bad = bad
        self.good = True
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        if self.good:
            return T0 + timedelta(seconds=self.calls)
        if callable(self.bad):
            return self.bad()  # type: ignore[no-any-return]
        return self.bad  # type: ignore[return-value]


_BAD_CLOCK_PARAMS = pytest.mark.parametrize(
    ("bad", "cause"), [(b, c) for _, b, c in _BAD_CLOCKS], ids=[i for i, _, _ in _BAD_CLOCKS]
)


@_BAD_CLOCK_PARAMS
def test_a_bad_clock_makes_submit_raise_clock_error_and_records_nothing(
    bad: object, cause: type[BaseException]
) -> None:
    clock = _SwitchableClock(bad)
    clock.good = False
    broker = FakeBroker(clock=clock, price_of=price_of)

    with pytest.raises(ClockError) as caught:
        broker.submit(make_request())
    assert not isinstance(caught.value, ValueError)
    assert isinstance(caught.value, SystemFaultError)
    assert isinstance(caught.value.__cause__, cause)

    # No order recorded: unknown to get_order and cancel, no fill, no position.
    with pytest.raises(UnknownOrderError):
        broker.get_order("co-1")
    with pytest.raises(UnknownOrderError):
        broker.cancel("co-1")
    assert broker.open_orders() == []
    assert broker.fills() == []
    assert broker.positions() == {}

    # The client_order_id is reusable once the clock is good.
    clock.good = True
    order = broker.submit(make_request())
    assert order.client_order_id == "co-1"
    assert order.status is OrderStatus.FILLED


@_BAD_CLOCK_PARAMS
def test_a_bad_clock_makes_submit_raise_clock_error_without_auto_fill(
    bad: object, cause: type[BaseException]
) -> None:
    clock = _SwitchableClock(bad)
    clock.good = False
    broker = FakeBroker(clock=clock, price_of=price_of, auto_fill=False)
    with pytest.raises(ClockError) as caught:
        broker.submit(make_request())
    assert isinstance(caught.value.__cause__, cause)
    with pytest.raises(UnknownOrderError):
        broker.get_order("co-1")
    assert broker.open_orders() == []
    assert broker.fills() == []
    assert broker.positions() == {}
    clock.good = True
    assert broker.submit(make_request()).status is OrderStatus.ACCEPTED


@_BAD_CLOCK_PARAMS
def test_a_bad_clock_makes_simulate_fill_raise_clock_error_and_changes_nothing(
    bad: object, cause: type[BaseException]
) -> None:
    clock = _SwitchableClock(bad)
    broker = FakeBroker(clock=clock, price_of=price_of, auto_fill=False)
    held = broker.submit(make_request(client_order_id="held", symbol="MSFT"))
    broker.simulate_fill(held.client_order_id)  # a position exists before the fault
    order = broker.submit(make_request())
    assert order.status is OrderStatus.ACCEPTED
    fills_before, positions_before = broker.fills(), broker.positions()
    cash_before = broker.account().cash

    clock.good = False
    with pytest.raises(ClockError) as caught:
        broker.simulate_fill(order.client_order_id)
    assert not isinstance(caught.value, ValueError)
    assert isinstance(caught.value.__cause__, cause)

    # No fill recorded, positions and cash unchanged, and the order still
    # ACCEPTED and otherwise unchanged: it fills normally once the clock is good.
    assert broker.get_order(order.client_order_id) == order
    assert broker.open_orders() == [order]
    assert broker.fills() == fills_before
    assert broker.positions() == positions_before
    clock.good = True
    assert broker.account().cash == cash_before
    filled = broker.simulate_fill(order.client_order_id)
    assert filled.status is OrderStatus.FILLED
    assert (filled.client_order_id, filled.broker_order_id, filled.submitted_at) == (
        order.client_order_id,
        order.broker_order_id,
        order.submitted_at,
    )
    assert len(broker.fills()) == len(fills_before) + 1


def test_a_clock_error_names_the_clock_and_only_the_cause_type() -> None:
    def leaky() -> datetime:
        raise RuntimeError("GET https://broker.example/clock?key=do-not-print")

    broker = FakeBroker(clock=leaky, price_of=price_of)
    with pytest.raises(ClockError, match=r"^clock failed: RuntimeError$") as caught:
        broker.submit(make_request())
    assert "do-not-print" not in str(caught.value)
    assert "do-not-print" in str(caught.value.__cause__)  # the chain keeps the detail


def test_a_base_exception_from_the_clock_is_not_turned_into_a_clock_error() -> None:
    # ADR 0007 point 1: `BaseException` (KeyboardInterrupt, SystemExit)
    # propagates untouched and is never classified.
    def interrupted() -> datetime:
        raise KeyboardInterrupt

    broker = FakeBroker(clock=interrupted, price_of=price_of)
    with pytest.raises(KeyboardInterrupt):
        broker.submit(make_request())


def test_the_clock_is_read_once_per_submit_and_once_per_simulate_fill() -> None:
    clock = _SwitchableClock(None)
    broker = FakeBroker(clock=clock, price_of=price_of, auto_fill=False)
    order = broker.submit(make_request())
    assert clock.calls == 1
    broker.simulate_fill(order.client_order_id)
    assert clock.calls == 2
    auto = FakeBroker(clock=clock, price_of=price_of)
    auto.submit(make_request())
    assert clock.calls == 3


def test_non_utc_clock_is_normalized_to_utc() -> None:
    eastern = T0.astimezone(ZoneInfo("America/New_York"))

    def clock() -> datetime:
        return eastern

    broker = FakeBroker(clock=clock, price_of=price_of)
    order = broker.submit(make_request())

    assert order.submitted_at == T0
    assert order.submitted_at.tzinfo is UTC
    assert broker.fills()[0].filled_at.tzinfo is UTC

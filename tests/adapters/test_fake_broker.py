"""Tests for the `Broker` interface and `FakeBroker` (T20).

Covers: abstractness, idempotent `submit` on a repeated `client_order_id`
(including against an open or cancelled original), `cancel`/`simulate_fill`
transitions and their error cases, fill/positions accounting (fill order,
not submission order; exact netting; short positions), state isolation
from returned collections, input validation (quantity, price, side,
symbol, tz-aware timestamps normalized to UTC), symbol case
canonicalization so `aapl` and `AAPL` net as one position (issue #38), and, at the broker level,
an aware timestamp that overflows once converted to UTC (issue #43)
raising `ValueError` (not `OverflowError`) from `Order`/`Fill`
construction, and a bad clock (naive, overflowing, not a `datetime`, or
raising) making `FakeBroker.submit`/`simulate_fill` raise `ClockError`
with no state changed (ADR 0007 point 4, T46).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
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
from tradepartner.errors import ClockError, SystemFaultError

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


def test_quantity_huge_int_raises_value_error_not_overflow_error() -> None:
    # math.isfinite raises OverflowError (not a bool) on an int too large to
    # convert to float; that must surface as the same ValueError as any
    # other invalid quantity, not leak as an unhandled OverflowError.
    with pytest.raises(ValueError, match="quantity"):
        make_request(quantity=10**400)


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


# --- Symbol canonicalization (issue #38) -----------------------------------


@pytest.mark.parametrize("raw", ["aapl", "Aapl", "AAPL"])
def test_symbol_is_canonicalized_to_upper_case_on_every_value_object(raw: str) -> None:
    assert make_request(symbol=raw).symbol == "AAPL"
    assert Position(symbol=raw, quantity=1).symbol == "AAPL"
    order = Order(
        client_order_id="co-1",
        symbol=raw,
        side=Side.BUY,
        quantity=1,
        price=1.0,
        status=OrderStatus.OPEN,
        submitted_at=T0,
    )
    assert order.symbol == "AAPL"
    fill = Fill(
        client_order_id="co-1", symbol=raw, side=Side.BUY, quantity=1, price=1.0, filled_at=T0
    )
    assert fill.symbol == "AAPL"


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
        Order(
            client_order_id="co-1",
            symbol="AAPL",
            side=Side.BUY,
            quantity=1,
            price=1.0,
            status=OrderStatus.OPEN,
            submitted_at=overflowing,
        )
    assert isinstance(excinfo.value.__cause__, OverflowError)


@pytest.mark.parametrize("overflowing", _OVERFLOWING_DATETIMES)
def test_utc_overflow_raises_value_error_on_fill(overflowing: datetime) -> None:
    with pytest.raises(ValueError, match=r"filled_at=.*out of the range") as excinfo:
        Fill(
            client_order_id="co-1",
            symbol="AAPL",
            side=Side.BUY,
            quantity=1,
            price=1.0,
            filled_at=overflowing,
        )
    assert isinstance(excinfo.value.__cause__, OverflowError)


# --- Clock faults (ADR 0007 point 4, plan T46) ---------------------------------
#
# Every clock reading is validated where it is read; any failure of the call
# or the validation is a `ClockError` (a `SystemFaultError`, never a
# `ValueError`), raised before any `Order` or `Fill` is built and before any
# state changes. These replace #63's clock tests, which expected `ValueError`.


def _raising_clock() -> datetime:
    raise RuntimeError("clock source unavailable")


#: (id, a bad clock reading or a callable that raises, the original error type)
_BAD_CLOCKS: list[tuple[str, object, type[BaseException]]] = [
    ("naive", datetime(2026, 1, 5, 15, 0), ValueError),  # noqa: DTZ001
    ("overflow-min", _OVERFLOWING_DATETIMES[0], ValueError),
    ("overflow-max", _OVERFLOWING_DATETIMES[1], ValueError),
    ("not-a-datetime-str", "2026-01-05T15:00:00+00:00", AttributeError),
    ("not-a-datetime-none", None, AttributeError),
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


@pytest.mark.parametrize(
    ("bad", "cause"), [(b, c) for _, b, c in _BAD_CLOCKS], ids=[i for i, _, _ in _BAD_CLOCKS]
)
def test_a_bad_clock_makes_submit_raise_clock_error_and_records_nothing(
    bad: object, cause: type[BaseException]
) -> None:
    clock = _SwitchableClock(bad)
    clock.good = False
    broker = FakeBroker(clock=clock)

    with pytest.raises(ClockError) as caught:
        broker.submit(make_request())
    assert not isinstance(caught.value, ValueError)
    assert isinstance(caught.value, SystemFaultError)
    assert isinstance(caught.value.__cause__, cause)

    # No order recorded: unknown to cancel, no fill, no position.
    with pytest.raises(UnknownOrderError):
        broker.cancel("co-1")
    assert broker.fills() == []
    assert broker.positions() == {}

    # The client_order_id is reusable once the clock is good.
    clock.good = True
    order = broker.submit(make_request())
    assert order.client_order_id == "co-1"
    assert order.status is OrderStatus.FILLED


@pytest.mark.parametrize(
    ("bad", "cause"), [(b, c) for _, b, c in _BAD_CLOCKS], ids=[i for i, _, _ in _BAD_CLOCKS]
)
def test_a_bad_clock_makes_submit_raise_clock_error_without_auto_fill(
    bad: object, cause: type[BaseException]
) -> None:
    clock = _SwitchableClock(bad)
    clock.good = False
    broker = FakeBroker(clock=clock, auto_fill=False)
    with pytest.raises(ClockError):
        broker.submit(make_request())
    with pytest.raises(UnknownOrderError):
        broker.cancel("co-1")


@pytest.mark.parametrize(
    ("bad", "cause"), [(b, c) for _, b, c in _BAD_CLOCKS], ids=[i for i, _, _ in _BAD_CLOCKS]
)
def test_a_bad_clock_makes_simulate_fill_raise_clock_error_and_changes_nothing(
    bad: object, cause: type[BaseException]
) -> None:
    clock = _SwitchableClock(bad)
    broker = FakeBroker(clock=clock, auto_fill=False)
    held = broker.submit(make_request(client_order_id="held", symbol="MSFT"))
    held = broker.simulate_fill(held.client_order_id)  # a position exists before the fault
    order = broker.submit(make_request())
    assert order.status is OrderStatus.OPEN
    fills_before, positions_before = broker.fills(), broker.positions()

    clock.good = False
    with pytest.raises(ClockError) as caught:
        broker.simulate_fill(order.client_order_id)
    assert not isinstance(caught.value, ValueError)
    assert isinstance(caught.value.__cause__, cause)

    # No fill recorded, positions unchanged, and the order still OPEN and
    # otherwise unchanged: it fills normally once the clock is good.
    assert broker.fills() == fills_before
    assert broker.positions() == positions_before
    clock.good = True
    filled = broker.simulate_fill(order.client_order_id)
    assert filled == replace(order, status=OrderStatus.FILLED)
    assert len(broker.fills()) == len(fills_before) + 1


def test_a_clock_error_names_the_clock() -> None:
    broker = FakeBroker(clock=lambda: datetime(2026, 1, 5, 15, 0))  # noqa: DTZ001
    with pytest.raises(ClockError, match="clock"):
        broker.submit(make_request())


def test_a_base_exception_from_the_clock_is_not_turned_into_a_clock_error() -> None:
    # ADR 0007 point 1: `BaseException` (KeyboardInterrupt, SystemExit)
    # propagates untouched and is never classified.
    def interrupted() -> datetime:
        raise KeyboardInterrupt

    broker = FakeBroker(clock=interrupted)
    with pytest.raises(KeyboardInterrupt):
        broker.submit(make_request())


def test_the_clock_is_read_once_per_submit_and_once_per_simulate_fill() -> None:
    clock = _SwitchableClock(None)
    broker = FakeBroker(clock=clock, auto_fill=False)
    order = broker.submit(make_request())
    assert clock.calls == 1
    broker.simulate_fill(order.client_order_id)
    assert clock.calls == 2
    auto = FakeBroker(clock=clock)
    auto.submit(make_request())
    assert clock.calls == 3


def test_non_utc_clock_is_normalized_to_utc() -> None:
    eastern = T0.astimezone(ZoneInfo("America/New_York"))

    def clock() -> datetime:
        return eastern

    broker = FakeBroker(clock=clock)
    order = broker.submit(make_request())

    assert order.submitted_at == T0
    assert order.submitted_at.tzinfo is UTC
    assert broker.fills()[0].filled_at.tzinfo is UTC

"""Tests for `FakeBroker` scripting (paper-trading plan T46c, #287).

Each instruction is set up through the scripting API and observed only
through the `Broker` methods (`submit`, `cancel`, `get_order`,
`open_orders`, `fills`, `positions`, `account`, `assets`), which is how the
wrapper tests (T58, T60 to T61b) will see it: accept, fill at a price,
partially fill, expire, reject, vanish, a transport error before or after
the book records the order, a held `pending_cancel` (mapped `ACCEPTED`)
that may fill before completing; `buying_power` apart from `cash`; cash
rounded to the cent per fill; `fills()` lagging `get_order`; per-session
`Asset` flags; the submit hook; the call log.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from tradepartner.adapters.broker import (
    Asset,
    DuplicateClientOrderIdError,
    OrderNotOpenError,
    OrderRequest,
    OrderStatus,
    Side,
    UnknownOrderError,
)
from tradepartner.adapters.fake_broker import (
    Accept,
    BrokerCall,
    Expire,
    FakeBroker,
    FakeTransportError,
    FillAt,
    HoldCancel,
    PartialFill,
    Reject,
    TransportFault,
    Vanish,
)
from tradepartner.errors import ClockError

T0 = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)  # Monday 10:00 New York
PRICES = {"AAPL": 100.0, "MSFT": 200.0}


class Clock:
    """A settable clock: returns `now` and never advances on its own."""

    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_broker(*, auto_fill: bool = False, **kwargs: object) -> tuple[FakeBroker, Clock]:
    clock = Clock()
    broker = FakeBroker(
        clock=clock,
        price_of=PRICES.__getitem__,
        auto_fill=auto_fill,
        **kwargs,  # type: ignore[arg-type]
    )
    return broker, clock


def buy(cid: str, *, quantity: float | None = 10.0, notional: float | None = None) -> OrderRequest:
    return OrderRequest(
        client_order_id=cid, symbol="AAPL", side=Side.BUY, quantity=quantity, notional=notional
    )


# --- submit-time outcomes, for the next submit or by id ---------------------


def test_next_submit_instructions_apply_in_order_then_default() -> None:
    broker, _ = make_broker(auto_fill=True)
    broker.script(Accept())
    broker.script(Reject())

    assert broker.submit(buy("a")).status is OrderStatus.ACCEPTED
    assert broker.submit(buy("b")).status is OrderStatus.REJECTED
    # Queue empty: the constructor's default (auto_fill) applies again.
    assert broker.submit(buy("c")).status is OrderStatus.FILLED
    assert [o.client_order_id for o in broker.open_orders()] == ["a"]
    assert broker.get_order("b").status is OrderStatus.REJECTED


def test_instruction_by_id_wins_over_the_next_submit_queue() -> None:
    broker, _ = make_broker()
    broker.script(Expire())
    broker.script(Reject(), client_order_id="b")

    assert broker.submit(buy("b")).status is OrderStatus.REJECTED
    assert broker.submit(buy("a")).status is OrderStatus.EXPIRED
    assert broker.open_orders() == []
    assert broker.fills() == []
    assert broker.positions() == {}


def test_fill_at_a_price_fills_the_whole_order_at_that_price() -> None:
    broker, _ = make_broker()
    broker.script(FillAt(price=101.25), client_order_id="a")

    order = broker.submit(buy("a", quantity=4.0))

    assert order.status is OrderStatus.FILLED
    assert broker.get_order("a") == order
    assert (order.filled_quantity, order.filled_avg_price, order.filled_at) == (4.0, 101.25, T0)
    [fill] = broker.fills()
    assert (fill.quantity, fill.price, fill.client_order_id) == (4.0, 101.25, "a")
    assert broker.positions()["AAPL"].quantity == 4.0
    assert broker.account().cash == 100_000.0 - 405.0


def test_fill_at_without_a_price_uses_price_of_and_sizes_a_notional_order() -> None:
    broker, _ = make_broker()
    broker.script(FillAt())

    order = broker.submit(buy("a", quantity=None, notional=250.0))

    assert (order.filled_quantity, order.filled_avg_price) == (2.5, 100.0)


def test_partial_fill_is_accepted_with_fill_fields_and_stays_open() -> None:
    broker, _ = make_broker()
    broker.script(PartialFill(quantity=3.0, avg_price=99.0))

    order = broker.submit(buy("a", quantity=10.0))

    assert order.status is OrderStatus.ACCEPTED
    assert (order.filled_quantity, order.filled_avg_price) == (3.0, 99.0)
    assert order.filled_at is None  # Alpaca sets it only once filled
    assert broker.open_orders() == [order]
    assert [(f.quantity, f.price) for f in broker.fills()] == [(3.0, 99.0)]
    assert broker.positions()["AAPL"].quantity == 3.0


def test_apply_on_the_book_accumulates_partials_then_completes() -> None:
    broker, clock = make_broker()
    broker.submit(buy("a", quantity=10.0))

    clock.now = T0 + timedelta(minutes=1)
    broker.apply("a", PartialFill(quantity=4.0, avg_price=100.0))
    clock.now = T0 + timedelta(minutes=2)
    broker.apply("a", FillAt(price=110.0))

    order = broker.get_order("a")
    assert order.status is OrderStatus.FILLED
    assert order.filled_quantity == 10.0
    assert order.filled_avg_price == pytest.approx((4 * 100.0 + 6 * 110.0) / 10)
    assert order.filled_at == T0 + timedelta(minutes=2)
    assert [(f.quantity, f.price) for f in broker.fills()] == [(4.0, 100.0), (6.0, 110.0)]
    assert broker.open_orders() == []


def test_apply_expire_or_reject_on_the_book_keeps_earlier_fills() -> None:
    broker, _ = make_broker()
    broker.script(PartialFill(quantity=2.0, avg_price=100.0), client_order_id="a")
    broker.submit(buy("a"))
    broker.submit(buy("b"))

    broker.apply("a", Expire())
    broker.apply("b", Reject())

    expired = broker.get_order("a")
    assert (expired.status, expired.filled_quantity) == (OrderStatus.EXPIRED, 2.0)
    assert broker.get_order("b").status is OrderStatus.REJECTED
    assert broker.open_orders() == []
    with pytest.raises(OrderNotOpenError):
        broker.apply("a", FillAt())
    with pytest.raises(UnknownOrderError):
        broker.apply("zzz", Expire())


def test_a_vanished_order_is_returned_then_forgotten() -> None:
    broker, _ = make_broker()
    broker.script(Vanish(), client_order_id="a")

    order = broker.submit(buy("a"))

    assert order.status is OrderStatus.ACCEPTED
    assert order.client_order_id == "a"
    with pytest.raises(UnknownOrderError):
        broker.get_order("a")
    with pytest.raises(UnknownOrderError):
        broker.cancel("a")
    assert broker.open_orders() == []
    assert broker.fills() == []


def test_apply_vanish_forgets_an_order_already_on_the_book() -> None:
    broker, _ = make_broker()
    broker.submit(buy("a"))

    broker.apply("a", Vanish())

    assert broker.open_orders() == []
    with pytest.raises(UnknownOrderError):
        broker.get_order("a")
    # Forgotten, so the id is free again.
    assert broker.submit(buy("a")).status is OrderStatus.ACCEPTED


def test_an_order_with_fills_cannot_vanish() -> None:
    broker, _ = make_broker()
    broker.script(PartialFill(quantity=1.0, avg_price=100.0))
    broker.submit(buy("a"))

    with pytest.raises(ValueError, match="has fills"):
        broker.apply("a", Vanish())
    assert broker.get_order("a").filled_quantity == 1.0


def test_partials_reaching_the_quantity_fill_the_order_and_beyond_it_raise() -> None:
    broker, clock = make_broker()
    broker.script(PartialFill(quantity=6.0, avg_price=100.0))
    broker.submit(buy("a", quantity=10.0))
    broker.submit(buy("b", quantity=10.0))

    with pytest.raises(ValueError, match="above its quantity"):
        broker.apply("b", PartialFill(quantity=10.5, avg_price=100.0))
    clock.now = T0 + timedelta(minutes=1)
    broker.apply("a", PartialFill(quantity=4.0, avg_price=100.0))

    order = broker.get_order("a")
    assert (order.status, order.filled_quantity) == (OrderStatus.FILLED, 10.0)
    assert order.filled_at == T0 + timedelta(minutes=1)
    assert broker.get_order("b").filled_quantity is None


def test_scripting_an_id_already_on_the_book_points_to_apply() -> None:
    broker, _ = make_broker()
    broker.submit(buy("a"))

    with pytest.raises(ValueError, match="use apply"):
        broker.script(Expire(), client_order_id="a")


def test_transport_fault_before_the_book_records_leaves_nothing() -> None:
    broker, _ = make_broker()
    broker.script(TransportFault())

    with pytest.raises(FakeTransportError):
        broker.submit(buy("a"))

    with pytest.raises(UnknownOrderError):
        broker.get_order("a")
    assert broker.open_orders() == []
    # Never received: the id is free, and the fault was consumed.
    assert broker.submit(buy("a")).status is OrderStatus.ACCEPTED


def test_transport_fault_after_the_book_records_keeps_the_order() -> None:
    broker, _ = make_broker()
    broker.script(TransportFault(after_record=True), client_order_id="a")

    with pytest.raises(FakeTransportError):
        broker.submit(buy("a"))

    held = broker.get_order("a")
    assert held.status is OrderStatus.ACCEPTED
    assert broker.open_orders() == [held]
    with pytest.raises(DuplicateClientOrderIdError):
        broker.submit(buy("a"))


def test_transport_fault_raises_the_scripted_error_afresh_each_time() -> None:
    broker, _ = make_broker()
    fault = TransportFault(error=TimeoutError("read timed out"))
    broker.script(fault)
    broker.script(fault)

    with pytest.raises(TimeoutError, match="read timed out") as first:
        broker.submit(buy("a"))
    with pytest.raises(TimeoutError) as second:
        broker.submit(buy("a"))
    assert first.value is not second.value


def test_a_clock_fault_consumes_no_instruction() -> None:
    broker, clock = make_broker()
    broker.script(Reject())
    clock.now = datetime(2026, 1, 5, 15, 0)  # noqa: DTZ001 - naive on purpose

    with pytest.raises(ClockError):
        broker.submit(buy("a"))

    clock.now = T0
    assert broker.submit(buy("a")).status is OrderStatus.REJECTED


def test_a_bad_fill_price_consumes_no_instruction_and_changes_nothing() -> None:
    broker = FakeBroker(clock=Clock(), price_of=lambda symbol: float("nan"), auto_fill=False)
    broker.script(FillAt())

    with pytest.raises(ValueError):
        broker.submit(buy("a"))

    with pytest.raises(UnknownOrderError):
        broker.get_order("a")
    assert broker.fills() == []
    with pytest.raises(ValueError):
        broker.submit(buy("a"))  # still queued


def test_script_validates_its_instructions() -> None:
    broker, _ = make_broker()
    with pytest.raises(ValueError):
        broker.script()
    with pytest.raises(ValueError):
        broker.script(Accept(), Reject())
    with pytest.raises(ValueError):
        broker.script(HoldCancel(), HoldCancel())
    with pytest.raises(ValueError):
        PartialFill(quantity=0.0, avg_price=1.0)
    with pytest.raises(ValueError):
        FillAt(price=-1.0)
    with pytest.raises(ValueError):
        broker.apply("a", TransportFault())  # type: ignore[arg-type]


# --- held cancels -------------------------------------------------------------


def test_a_held_cancel_maps_accepted_until_completed() -> None:
    broker, _ = make_broker()
    broker.script(HoldCancel(), client_order_id="a")
    broker.submit(buy("a"))

    assert broker.cancel("a") is None

    pending = broker.get_order("a")
    assert pending.status is OrderStatus.ACCEPTED
    assert broker.open_orders() == [pending]

    broker.complete_cancel("a")
    assert broker.get_order("a").status is OrderStatus.CANCELLED
    assert broker.open_orders() == []
    with pytest.raises(OrderNotOpenError):
        broker.complete_cancel("a")


def test_a_held_cancel_can_fill_before_completing() -> None:
    broker, clock = make_broker()
    broker.script(Accept(), HoldCancel(fill=PartialFill(quantity=4.0, avg_price=101.0)))
    broker.submit(buy("a", quantity=10.0))

    clock.now = T0 + timedelta(seconds=30)
    broker.cancel("a")

    pending = broker.get_order("a")
    assert (pending.status, pending.filled_quantity) == (OrderStatus.ACCEPTED, 4.0)
    assert pending.filled_at is None
    assert [(f.quantity, f.price) for f in broker.fills()] == [(4.0, 101.0)]

    broker.complete_cancel("a")
    done = broker.get_order("a")
    assert (done.status, done.filled_quantity) == (OrderStatus.CANCELLED, 4.0)
    assert broker.positions()["AAPL"].quantity == 4.0


def test_apply_hold_cancel_on_the_book_and_complete_without_a_cancel() -> None:
    broker, _ = make_broker()
    broker.submit(buy("a"))
    broker.apply("a", HoldCancel())
    with pytest.raises(ValueError, match="no cancel"):
        broker.complete_cancel("a")

    broker.cancel("a")
    broker.cancel("a")  # a repeat request while pending changes nothing
    assert broker.get_order("a").status is OrderStatus.ACCEPTED


def test_an_unheld_cancel_still_completes_at_once() -> None:
    broker, _ = make_broker()
    broker.submit(buy("a"))

    broker.cancel("a")

    assert broker.get_order("a").status is OrderStatus.CANCELLED


# --- account -------------------------------------------------------------------


def test_buying_power_is_set_apart_from_cash() -> None:
    broker, _ = make_broker(cash=1_000.0, buying_power=4_000.0)
    broker.script(FillAt())
    broker.submit(buy("a", quantity=2.0))

    account = broker.account()
    assert (account.cash, account.buying_power) == (800.0, 4_000.0)

    broker.set_buying_power(None)
    assert broker.account().buying_power == 800.0
    broker.set_buying_power(9_999.0)
    assert broker.account().buying_power == 9_999.0


def test_cash_is_rounded_to_the_cent_per_fill_when_told() -> None:
    exact, _ = make_broker(cash=1_000.0)
    rounded, _ = make_broker(cash=1_000.0, round_cash_to_cent=True)
    for broker in (exact, rounded):
        broker.script(FillAt(price=33.333))
        broker.submit(buy("a", quantity=0.5))  # 16.6665 per share lot
        broker.script(FillAt(price=33.333))
        broker.submit(buy("b", quantity=0.5))

    assert exact.account().cash == pytest.approx(1_000.0 - 33.333)
    # Each fill rounds half-up on its own: 16.6665 -> 16.67, twice.
    assert rounded.account().cash == 966.66


# --- lagging fills --------------------------------------------------------------


def test_fills_lag_get_order_by_one_read_when_told() -> None:
    broker, _ = make_broker()
    broker.lag_fills(1)
    broker.script(FillAt())
    broker.submit(buy("a"))

    assert broker.get_order("a").status is OrderStatus.FILLED
    assert broker.fills() == []  # the first read misses it
    [fill] = broker.fills()  # the second delivers it
    assert fill.client_order_id == "a"
    assert len(broker.fills()) == 1


def test_lag_applies_to_fills_recorded_after_it_is_set() -> None:
    broker, clock = make_broker()
    broker.lag_fills(1)
    broker.script(FillAt())
    broker.submit(buy("early"))
    broker.lag_fills(0)
    clock.now = T0 + timedelta(minutes=1)
    broker.script(FillAt())
    broker.submit(buy("late"))

    assert [f.client_order_id for f in broker.fills()] == ["late"]
    assert [f.client_order_id for f in broker.fills(since=T0)] == ["early", "late"]


def test_a_fill_lagged_forever_is_never_delivered() -> None:
    broker, _ = make_broker()
    broker.lag_fills(None)
    broker.script(FillAt())
    broker.submit(buy("a"))

    for _ in range(5):
        assert broker.fills() == []
    assert broker.get_order("a").filled_quantity == 10.0
    with pytest.raises(ValueError):
        broker.lag_fills(-1)


def test_reveal_hidden_fills_delivers_already_recorded_fills_on_the_next_read() -> None:
    """#742: the feed catches up. Fills recorded under a finite or a forever
    lag surface on the next `fills()`; the lag for later fills is kept, and
    the hook is not a `Broker` call."""
    broker, clock = make_broker()
    broker.lag_fills(None)
    broker.script(FillAt())
    broker.submit(buy("forever"))
    broker.lag_fills(3)
    clock.now = T0 + timedelta(minutes=1)
    broker.script(FillAt())
    broker.submit(buy("three"))
    assert broker.fills() == []
    calls_before = len(broker.calls)

    broker.reveal_hidden_fills()

    assert len(broker.calls) == calls_before
    assert [f.client_order_id for f in broker.fills()] == ["forever", "three"]
    clock.now = T0 + timedelta(minutes=2)
    broker.script(FillAt())
    broker.submit(buy("later"))  # still recorded under lag_fills(3)
    assert [f.client_order_id for f in broker.fills()] == ["forever", "three"]


# --- assets per session ------------------------------------------------------------


def test_asset_flags_are_settable_per_session() -> None:
    broker, clock = make_broker()
    halted = Asset(tradable=False, fractionable=False, status="inactive", cusip="037833100")
    live = Asset(tradable=True, fractionable=True, status="active", cusip="037833100")
    broker.set_asset("aapl", live)
    broker.set_asset("AAPL", halted, from_session=date(2026, 1, 6))
    broker.set_asset("AAPL", live, from_session=date(2026, 1, 8))

    by_day = {}
    for day in (5, 6, 7, 8):
        clock.now = datetime(2026, 1, day, 15, 0, tzinfo=UTC)
        by_day[day] = broker.assets(["AAPL", "MSFT"])["AAPL"]
    assert by_day == {5: live, 6: halted, 7: halted, 8: live}
    assert broker.assets(["MSFT"])["MSFT"].tradable is True


def test_asset_session_is_the_new_york_date() -> None:
    broker, clock = make_broker()
    halted = Asset(tradable=False, fractionable=True, status="active", cusip=None)
    broker.set_asset("AAPL", halted, from_session=date(2026, 1, 6))

    clock.now = datetime(2026, 1, 6, 3, 0, tzinfo=UTC)  # 22:00 on the 5th in New York
    assert broker.assets(["AAPL"])["AAPL"].tradable is True
    clock.now = datetime(2026, 1, 6, 14, 0, tzinfo=UTC)
    assert broker.assets(["AAPL"])["AAPL"].tradable is False


def test_set_asset_rejects_a_non_asset() -> None:
    broker, _ = make_broker()
    with pytest.raises(TypeError):
        broker.set_asset("AAPL", object())  # type: ignore[arg-type]


# --- submit hook and call log --------------------------------------------------------


def test_the_submit_hook_runs_before_anything_is_recorded() -> None:
    seen: list[tuple[str, int]] = []
    broker, _ = make_broker()

    def hook(request: OrderRequest) -> None:
        seen.append((request.client_order_id, len(broker.open_orders())))

    broker.on_submit = hook
    broker.submit(buy("a"))
    broker.submit(buy("b"))
    assert seen == [("a", 0), ("b", 1)]


def test_a_raising_submit_hook_records_nothing() -> None:
    def hook(request: OrderRequest) -> None:
        raise AssertionError("store state wrong")

    broker, _ = make_broker(on_submit=hook)
    broker.script(Reject())
    with pytest.raises(AssertionError):
        broker.submit(buy("a"))

    broker.on_submit = None
    assert broker.submit(buy("a")).status is OrderStatus.REJECTED


def test_the_call_log_records_every_broker_method_in_order_with_arguments() -> None:
    broker, _ = make_broker()
    request = buy("a")
    broker.submit(request)
    broker.get_order("a")
    broker.open_orders()
    broker.fills(since=T0)
    broker.fills()
    broker.positions()
    broker.account()
    broker.assets(["aapl", "MSFT"])
    broker.cancel("a")
    with pytest.raises(UnknownOrderError):
        broker.get_order("missing")
    broker.script(Accept())  # scripting is not a Broker method: not logged

    assert broker.calls == (
        BrokerCall("submit", (request,)),
        BrokerCall("get_order", ("a",)),
        BrokerCall("open_orders", ()),
        BrokerCall("fills", (T0,)),
        BrokerCall("fills", (None,)),
        BrokerCall("positions", ()),
        BrokerCall("account", ()),
        BrokerCall("assets", (("aapl", "MSFT"),)),
        BrokerCall("cancel", ("a",)),
        BrokerCall("get_order", ("missing",)),
    )


def test_the_call_log_is_a_snapshot() -> None:
    broker, _ = make_broker()
    before = broker.calls
    broker.open_orders()
    assert before == ()
    assert len(broker.calls) == 1

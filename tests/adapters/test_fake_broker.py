"""Tests for `adapters.broker` and `adapters.fake_broker` (T20, incl.
review round 2 fixes: no-look-ahead in session fill/expiry, immutability,
atomic price validation, tz normalisation, and stricter `Order`/`Fill`
field validation).

No risk logic is exercised or expected here (ADR 0003 rule 7): the "sell
more than held" test documents that a negative position is exactly what
`FakeBroker` produces, on purpose, and that stopping it is the risk-gated
wrapper's job (Phase 4), not this adapter's.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from tradepartner.adapters.broker import (
    DuplicateOrderError,
    Fill,
    Order,
    OrderNotCancellableError,
    OrderNotFoundError,
)
from tradepartner.adapters.fake_broker import FakeBroker

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


def test_duplicate_client_order_id_after_fill_is_rejected() -> None:
    broker = FakeBroker()
    order = make_order()
    broker.submit(order)
    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})

    with pytest.raises(DuplicateOrderError):
        broker.submit(make_order(qty=1))


def test_duplicate_client_order_id_after_cancel_is_rejected() -> None:
    broker = FakeBroker()
    order = make_order()
    broker.submit(order)
    broker.cancel(order.client_order_id)

    with pytest.raises(DuplicateOrderError):
        broker.submit(make_order(qty=1))


# --- orders is a read-only view ------------------------------------------


def test_orders_property_is_read_only() -> None:
    broker = FakeBroker()
    order = make_order()
    broker.submit(order)

    with pytest.raises(TypeError):
        broker.orders["co-1"] = None  # type: ignore[index]


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
    assert fill.filled_at > order.submitted_at


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


def test_cancelled_order_is_not_filled_by_a_later_open() -> None:
    broker = FakeBroker()
    order = make_order()
    broker.submit(order)
    broker.cancel(order.client_order_id)

    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})

    assert broker.order_status(order.client_order_id) == "cancelled"
    assert broker.fills(since=OPEN_1) == []


def test_expired_order_is_not_filled_by_a_later_open() -> None:
    broker = FakeBroker()
    order = make_order(order_type="limit", side="buy", limit_price=Decimal("1"), qty=5)
    broker.submit(order)
    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})  # limit not satisfied
    broker.mark_session_close(CLOSE_1)  # expires
    assert broker.order_status(order.client_order_id) == "expired"

    # Even though this open's price would have satisfied the (now-dead) limit.
    broker.mark_session_open(OPEN_2, {"sec-aaa": Decimal("0.50")})

    assert broker.order_status(order.client_order_id) == "expired"
    assert broker.fills(since=OPEN_2) == []


# --- no look-ahead: eligibility by submitted_at -------------------------


def test_order_submitted_exactly_at_open_is_not_eligible_for_that_open() -> None:
    """`submitted_at` must be *strictly* before `session_open_ts`."""
    broker = FakeBroker()
    order = make_order(submitted_at=OPEN_1)
    broker.submit(order)

    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})

    assert broker.order_status(order.client_order_id) == "accepted"


def test_order_submitted_after_open_is_not_filled_that_session_but_is_the_next() -> None:
    broker = FakeBroker()
    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})  # nothing submitted yet

    mid_session_ts = OPEN_1 + timedelta(hours=1)  # placed after open(T), during session T
    order = make_order(submitted_at=mid_session_ts)
    broker.submit(order)

    broker.mark_session_close(CLOSE_1)  # session T closes; order was never eligible for T
    assert broker.order_status(order.client_order_id) == "accepted"

    broker.mark_session_open(OPEN_2, {"sec-aaa": Decimal("100")})  # session T+1 opens

    assert broker.order_status(order.client_order_id) == "filled"
    fill = broker.fills(since=OPEN_2)[0]
    assert fill.filled_at == OPEN_2
    assert fill.filled_at > order.submitted_at


def test_order_submitted_after_close_is_not_expired_by_a_repeated_close_call() -> None:
    """A DAY order placed after a session's close (i.e. for the next
    open) must not be expired by a later call that re-closes the same
    session at the same timestamp (non-decreasing timestamps allow a
    repeat)."""
    broker = FakeBroker()
    broker.mark_session_open(OPEN_1, {})
    broker.mark_session_close(CLOSE_1)

    late_order = make_order("co-late", submitted_at=CLOSE_1)
    broker.submit(late_order)

    broker.mark_session_close(CLOSE_1)  # redundant close call, same ts

    assert broker.order_status("co-late") == "accepted"

    # It is, however, eligible for (and fills at) the next session's open.
    broker.mark_session_open(OPEN_2, {"sec-aaa": Decimal("100")})
    assert broker.order_status("co-late") == "filled"


def test_session_timestamps_must_be_non_decreasing() -> None:
    broker = FakeBroker()
    broker.mark_session_open(OPEN_1, {})

    with pytest.raises(ValueError, match="non-decreasing"):
        broker.mark_session_close(T0)  # T0 is before OPEN_1


def test_session_timestamps_non_decreasing_check_spans_open_and_close() -> None:
    broker = FakeBroker()
    broker.mark_session_open(OPEN_1, {})
    broker.mark_session_close(CLOSE_1)

    with pytest.raises(ValueError, match="non-decreasing"):
        broker.mark_session_open(OPEN_1, {})  # earlier than CLOSE_1


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


def test_cancel_expired_order_raises() -> None:
    broker = FakeBroker()
    order = make_order(order_type="limit", side="buy", limit_price=Decimal("1"), qty=5)
    broker.submit(order)
    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})  # limit not satisfied
    broker.mark_session_close(CLOSE_1)  # expires
    assert broker.order_status(order.client_order_id) == "expired"

    with pytest.raises(OrderNotCancellableError):
        broker.cancel(order.client_order_id)


def test_cancel_already_cancelled_order_raises() -> None:
    broker = FakeBroker()
    order = make_order()
    broker.submit(order)
    broker.cancel(order.client_order_id)

    with pytest.raises(OrderNotCancellableError):
        broker.cancel(order.client_order_id)


def test_cancel_unknown_order_raises_order_not_found() -> None:
    broker = FakeBroker()

    with pytest.raises(OrderNotFoundError):
        broker.cancel("no-such-order")


def test_order_status_unknown_id_raises() -> None:
    broker = FakeBroker()

    with pytest.raises(OrderNotFoundError):
        broker.order_status("no-such-order")


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


def test_position_short_from_flat_avg_price_is_fill_price() -> None:
    broker = FakeBroker()
    sell = make_order("co-sell", side="sell", qty=10)
    broker.submit(sell)
    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("50")})

    position = broker.positions()[0]
    assert position.qty == Decimal(-10)
    assert position.avg_price == Decimal("50")


def test_position_add_to_short_weighted_average() -> None:
    broker = FakeBroker()
    sell1 = make_order("co-sell-1", side="sell", qty=10)
    broker.submit(sell1)
    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("50")})

    sell2 = make_order("co-sell-2", side="sell", qty=10, submitted_at=OPEN_1)
    broker.submit(sell2)
    broker.mark_session_open(OPEN_2, {"sec-aaa": Decimal("60")})

    position = broker.positions()[0]
    assert position.qty == Decimal(-20)
    assert position.avg_price == Decimal("55")  # (10*50 + 10*60) / 20


def test_position_cross_back_from_short_to_long_avg_price() -> None:
    broker = FakeBroker()
    sell = make_order("co-sell", side="sell", qty=10)
    broker.submit(sell)
    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("50")})  # short 10 @ 50

    buy = make_order("co-buy", side="buy", qty=25, submitted_at=OPEN_1)
    broker.submit(buy)
    broker.mark_session_open(OPEN_2, {"sec-aaa": Decimal("60")})  # covers 10, opens long 15 @ 60

    position = broker.positions()[0]
    assert position.qty == Decimal(15)
    assert position.avg_price == Decimal("60")


def test_position_symbol_refreshes_on_later_fill_with_different_symbol() -> None:
    broker = FakeBroker()
    buy1 = make_order("co-1", security_id="sec-aaa", symbol="AAA", side="buy", qty=5)
    broker.submit(buy1)
    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})

    buy2 = make_order(
        "co-2", security_id="sec-aaa", symbol="AAA-NEW", side="buy", qty=5, submitted_at=OPEN_1
    )
    broker.submit(buy2)
    broker.mark_session_open(OPEN_2, {"sec-aaa": Decimal("100")})

    position = broker.positions()[0]
    assert position.symbol == "AAA-NEW"


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


def test_fill_never_precedes_order_submission() -> None:
    broker = FakeBroker()
    order = make_order(submitted_at=T0)
    broker.submit(order)

    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})

    fill = broker.fills(since=OPEN_1)[0]
    assert fill.filled_at > order.submitted_at


# --- atomic price validation in mark_session_open -----------------------


def test_mark_session_open_bad_price_type_raises_and_changes_nothing() -> None:
    broker = FakeBroker()
    good_order = make_order("co-good", security_id="sec-aaa")
    broker.submit(good_order)

    with pytest.raises(TypeError):
        broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100"), "sec-bbb": 5})  # type: ignore[dict-item]

    assert broker.order_status("co-good") == "accepted"
    assert broker.fills(since=OPEN_1) == []


def test_mark_session_open_non_finite_price_raises_and_changes_nothing() -> None:
    broker = FakeBroker()
    good_order = make_order("co-good", security_id="sec-aaa")
    broker.submit(good_order)

    with pytest.raises(ValueError, match="finite"):
        broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100"), "sec-bbb": Decimal("NaN")})

    assert broker.order_status("co-good") == "accepted"
    assert broker.fills(since=OPEN_1) == []


def test_mark_session_open_non_positive_price_raises_and_changes_nothing() -> None:
    broker = FakeBroker()
    good_order = make_order("co-good", security_id="sec-aaa")
    broker.submit(good_order)

    with pytest.raises(ValueError, match=r"must be > 0"):
        broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100"), "sec-bbb": Decimal("-1")})

    assert broker.order_status("co-good") == "accepted"
    assert broker.fills(since=OPEN_1) == []


def test_mark_session_open_bad_price_does_not_advance_session_clock() -> None:
    """A failed `mark_session_open` (bad price) must not commit
    `session_open_ts` either — a later call with a valid `prices` dict at
    the *same* timestamp must not be rejected as non-monotonic."""
    broker = FakeBroker()
    order = make_order()
    broker.submit(order)

    with pytest.raises(ValueError, match="must be > 0"):
        broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("-1")})

    # Retried with a valid price at the same timestamp: succeeds.
    broker.mark_session_open(OPEN_1, {"sec-aaa": Decimal("100")})
    assert broker.order_status(order.client_order_id) == "filled"


# --- naive / non-UTC datetimes -----------------------------------------


class _BrokenTzInfo(tzinfo):
    """A `tzinfo` that is present but reports no UTC offset — the rarer
    "incomplete tzinfo" case `require_tz_aware` also rejects."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return "broken"


def test_order_submitted_at_naive_datetime_raises() -> None:
    with pytest.raises(ValidationError):
        make_order(submitted_at=datetime(2026, 1, 5, 14, 30))  # noqa: DTZ001


def test_order_submitted_at_tzinfo_without_utcoffset_raises() -> None:
    with pytest.raises(ValidationError):
        make_order(submitted_at=datetime(2026, 1, 5, 14, 30, tzinfo=_BrokenTzInfo()))


def test_order_submitted_at_non_utc_is_normalised_to_utc() -> None:
    eastern = timezone(timedelta(hours=-5))
    local_dt = datetime(2026, 1, 5, 9, 30, tzinfo=eastern)  # == 14:30 UTC
    order = make_order(submitted_at=local_dt)

    assert order.submitted_at == datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    assert order.submitted_at.tzinfo == UTC


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


# --- Order/Fill/Position field validation -------------------------------


def test_order_is_frozen() -> None:
    order = make_order()
    with pytest.raises(ValidationError):
        order.qty = Decimal(1)  # type: ignore[misc]


def test_fill_is_frozen() -> None:
    fill = Fill(client_order_id="co-1", qty=Decimal(1), price=Decimal(1), filled_at=T0)
    with pytest.raises(ValidationError):
        fill.price = Decimal(2)  # type: ignore[misc]


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


@pytest.mark.parametrize("bad_qty", [0, -1])
def test_order_qty_must_be_positive(bad_qty: int) -> None:
    with pytest.raises(ValidationError):
        make_order(qty=bad_qty)


def test_order_limit_price_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Order(
            client_order_id="co-1",
            security_id="sec-aaa",
            symbol="AAA",
            side="buy",
            qty=Decimal(1),
            order_type="limit",
            limit_price=Decimal(0),
            submitted_at=T0,
        )


def test_order_client_order_id_empty_rejected() -> None:
    with pytest.raises(ValidationError):
        make_order(client_order_id="")


def test_order_client_order_id_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        make_order(client_order_id="x" * 129)


def test_order_security_id_empty_rejected() -> None:
    with pytest.raises(ValidationError):
        make_order(security_id="")


def test_order_symbol_empty_rejected() -> None:
    with pytest.raises(ValidationError):
        make_order(symbol="")


def test_order_qty_float_rejected() -> None:
    kwargs: dict[str, Any] = dict(
        client_order_id="co-1",
        security_id="sec-aaa",
        symbol="AAA",
        side="buy",
        qty=1.5,
        order_type="market",
        limit_price=None,
        submitted_at=T0,
    )
    with pytest.raises(ValidationError):
        Order(**kwargs)


def test_order_limit_price_float_rejected() -> None:
    kwargs: dict[str, Any] = dict(
        client_order_id="co-1",
        security_id="sec-aaa",
        symbol="AAA",
        side="buy",
        qty=Decimal(1),
        order_type="limit",
        limit_price=99.5,
        submitted_at=T0,
    )
    with pytest.raises(ValidationError):
        Order(**kwargs)


def test_fill_qty_float_rejected() -> None:
    kwargs: dict[str, Any] = dict(client_order_id="co-1", qty=1.0, price=Decimal(1), filled_at=T0)
    with pytest.raises(ValidationError):
        Fill(**kwargs)


def test_fill_price_float_rejected() -> None:
    kwargs: dict[str, Any] = dict(client_order_id="co-1", qty=Decimal(1), price=1.0, filled_at=T0)
    with pytest.raises(ValidationError):
        Fill(**kwargs)


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
    assert position.avg_price == Decimal("100")

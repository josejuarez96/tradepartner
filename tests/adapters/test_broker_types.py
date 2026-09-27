"""The Phase 4 broker value types and interface (spec req 1, #33; plan T46b).

Pinned here: the order-status enum and its terminal set, the market DAY
`OrderRequest` (exactly one of `notional` and `quantity`, no price), the
`Order` fill fields, `Fill.broker_fill_id`, `Account`, `Asset`, and the
`Broker` ABC's methods (no clock member, ADR 0007 point 5).
"""

from __future__ import annotations

import inspect
from dataclasses import fields
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from tradepartner.adapters.broker import (
    TERMINAL_STATUSES,
    Account,
    Asset,
    Broker,
    Fill,
    Order,
    OrderRequest,
    OrderStatus,
    Side,
)

T0 = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def _order(**overrides: Any) -> Order:
    values: dict[str, Any] = {
        "client_order_id": "co-1",
        "symbol": "AAPL",
        "side": Side.BUY,
        "quantity": 10.0,
        "status": OrderStatus.ACCEPTED,
        "submitted_at": T0,
    }
    values.update(overrides)
    return Order(**values)


def _fill(**overrides: Any) -> Fill:
    values: dict[str, Any] = {
        "client_order_id": "co-1",
        "symbol": "AAPL",
        "side": Side.BUY,
        "quantity": 1.0,
        "price": 100.0,
        "filled_at": T0,
        "broker_fill_id": "f-1",
    }
    values.update(overrides)
    return Fill(**values)


# --- OrderStatus ----------------------------------------------------------------


def test_order_status_is_exactly_the_five_broker_states() -> None:
    assert {s.name for s in OrderStatus} == {
        "ACCEPTED",
        "FILLED",
        "EXPIRED",
        "REJECTED",
        "CANCELLED",
    }
    assert {s.value for s in OrderStatus} == {
        "accepted",
        "filled",
        "expired",
        "rejected",
        "cancelled",
    }


def test_no_pending_and_no_open_state() -> None:
    names = {s.name for s in OrderStatus}
    assert "PENDING" not in names and "OPEN" not in names


def test_the_terminal_set_is_exactly_the_last_four() -> None:
    assert (
        frozenset(
            {OrderStatus.FILLED, OrderStatus.EXPIRED, OrderStatus.REJECTED, OrderStatus.CANCELLED}
        )
        == TERMINAL_STATUSES
    )
    assert isinstance(TERMINAL_STATUSES, frozenset)
    assert OrderStatus.ACCEPTED not in TERMINAL_STATUSES


# --- OrderRequest -----------------------------------------------------------------


def test_a_request_by_quantity() -> None:
    request = OrderRequest("co-1", "aapl", Side.BUY, quantity=10)
    assert (request.symbol, request.quantity, request.notional) == ("AAPL", 10.0, None)


def test_a_request_by_notional() -> None:
    request = OrderRequest("co-1", "AAPL", Side.SELL, notional=1500.25)
    assert (request.notional, request.quantity) == (1500.25, None)


def test_a_request_with_both_notional_and_quantity_raises() -> None:
    with pytest.raises(ValueError, match="exactly one of notional and quantity"):
        OrderRequest("co-1", "AAPL", Side.BUY, notional=100.0, quantity=1)


def test_a_request_with_neither_notional_nor_quantity_raises() -> None:
    with pytest.raises(ValueError, match="exactly one of notional and quantity"):
        OrderRequest("co-1", "AAPL", Side.BUY)


@pytest.mark.parametrize("bad", [0, -1.0, float("nan"), float("inf"), True])
def test_notional_must_be_positive_finite(bad: float) -> None:
    with pytest.raises(ValueError, match="notional"):
        OrderRequest("co-1", "AAPL", Side.BUY, notional=bad)


def test_a_request_has_no_price_field() -> None:
    assert "price" not in {f.name for f in fields(OrderRequest)}
    assert [f.name for f in fields(OrderRequest)] == [
        "client_order_id",
        "symbol",
        "side",
        "notional",
        "quantity",
    ]


# --- Order ------------------------------------------------------------------------


def test_order_fields() -> None:
    assert [f.name for f in fields(Order)] == [
        "client_order_id",
        "symbol",
        "side",
        "notional",
        "quantity",
        "status",
        "submitted_at",
        "broker_order_id",
        "filled_quantity",
        "filled_avg_price",
        "filled_at",
    ]


def test_the_last_four_order_fields_default_to_none() -> None:
    order = _order()
    assert (
        order.broker_order_id,
        order.filled_quantity,
        order.filled_avg_price,
        order.filled_at,
    ) == (None, None, None, None)


def test_a_partial_fill_is_accepted_with_filled_quantity_and_average_price() -> None:
    order = _order(quantity=10, filled_quantity=4, filled_avg_price=101.5, broker_order_id="b-1")
    assert order.status is OrderStatus.ACCEPTED
    assert order.status not in TERMINAL_STATUSES
    assert (order.filled_quantity, order.filled_avg_price) == (4.0, 101.5)


def test_an_order_by_notional() -> None:
    order = _order(quantity=None, notional=1000.0)
    assert (order.notional, order.quantity) == (1000.0, None)


@pytest.mark.parametrize("sizes", [{"notional": 1.0, "quantity": 1.0}, {"quantity": None}])
def test_an_order_needs_exactly_one_of_notional_and_quantity(sizes: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="exactly one of notional and quantity"):
        _order(**sizes)


def test_filled_quantity_and_average_price_come_together() -> None:
    with pytest.raises(ValueError, match="filled_quantity and filled_avg_price"):
        _order(filled_quantity=1.0)
    with pytest.raises(ValueError, match="filled_quantity and filled_avg_price"):
        _order(filled_avg_price=1.0)


def test_a_filled_order_carries_its_fill_fields() -> None:
    with pytest.raises(ValueError, match="FILLED"):
        _order(status=OrderStatus.FILLED)
    with pytest.raises(ValueError, match="FILLED"):
        _order(status=OrderStatus.FILLED, filled_quantity=10, filled_avg_price=1.0)
    order = _order(
        status=OrderStatus.FILLED, filled_quantity=10, filled_avg_price=1.0, filled_at=T0
    )
    assert order.status in TERMINAL_STATUSES


@pytest.mark.parametrize("field_name", ["filled_quantity", "filled_avg_price"])
@pytest.mark.parametrize("bad", [0, -1.0, float("nan"), float("inf")])
def test_fill_amounts_must_be_positive_finite(field_name: str, bad: float) -> None:
    values = {"filled_quantity": 1.0, "filled_avg_price": 1.0, field_name: bad}
    with pytest.raises(ValueError, match=field_name):
        _order(**values)


def test_order_timestamps_are_utc() -> None:
    eastern = timezone(timedelta(hours=-5))
    order = _order(
        submitted_at=T0.astimezone(eastern),
        status=OrderStatus.FILLED,
        filled_quantity=10,
        filled_avg_price=1.0,
        filled_at=T0.astimezone(eastern),
    )
    assert order.submitted_at.tzinfo is UTC and order.filled_at is not None
    assert order.filled_at.tzinfo is UTC
    with pytest.raises(ValueError, match="filled_at"):
        _order(filled_at=datetime(2026, 1, 5))  # noqa: DTZ001


def test_status_is_coerced_from_its_value_and_rejects_others() -> None:
    assert _order(status="accepted").status is OrderStatus.ACCEPTED
    with pytest.raises(ValueError, match="status"):
        _order(status="open")


def test_broker_order_id_must_be_an_identifier_when_set() -> None:
    assert _order(broker_order_id="b-1").broker_order_id == "b-1"
    with pytest.raises(ValueError, match="broker_order_id"):
        _order(broker_order_id=" b-1")


# --- Fill ---------------------------------------------------------------------------


def test_fill_carries_a_broker_fill_id() -> None:
    assert _fill().broker_fill_id == "f-1"
    assert "broker_fill_id" in {f.name for f in fields(Fill)}


@pytest.mark.parametrize("bad", ["", " f-1", 7])
def test_broker_fill_id_must_be_an_identifier(bad: object) -> None:
    with pytest.raises(ValueError, match="broker_fill_id"):
        _fill(broker_fill_id=bad)


# --- Account and Asset ----------------------------------------------------------------


def test_account_shape() -> None:
    account = Account(
        account_id="acct-1",
        cash=1000.5,
        buying_power=2001,
        equity=1500.0,
        as_of=T0.astimezone(timezone(timedelta(hours=-5))),
    )
    assert [f.name for f in fields(Account)] == [
        "account_id",
        "cash",
        "buying_power",
        "equity",
        "as_of",
    ]
    assert account.buying_power == 2001.0 and isinstance(account.buying_power, float)
    assert account.as_of == T0 and account.as_of.tzinfo is UTC


@pytest.mark.parametrize("field_name", ["cash", "buying_power", "equity"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "1"])
def test_account_amounts_must_be_finite_numbers(field_name: str, bad: object) -> None:
    values: dict[str, Any] = {
        "account_id": "acct-1",
        "cash": 1.0,
        "buying_power": 1.0,
        "equity": 1.0,
        "as_of": T0,
    }
    values[field_name] = bad
    with pytest.raises(ValueError, match=field_name):
        Account(**values)


def test_account_cash_may_be_negative() -> None:
    assert Account("acct-1", -5.0, 0.0, 10.0, T0).cash == -5.0


def test_account_needs_an_id_and_an_aware_as_of() -> None:
    with pytest.raises(ValueError, match="account_id"):
        Account("", 1.0, 1.0, 1.0, T0)
    with pytest.raises(ValueError, match="as_of"):
        Account("acct-1", 1.0, 1.0, 1.0, datetime(2026, 1, 5))  # noqa: DTZ001


def test_asset_shape_and_nullable_cusip() -> None:
    assert [f.name for f in fields(Asset)] == ["tradable", "fractionable", "status", "cusip"]
    assert Asset(tradable=True, fractionable=False, status="active", cusip=None).cusip is None
    assert Asset(True, True, "active", "037833100").cusip == "037833100"


@pytest.mark.parametrize("field_name", ["tradable", "fractionable"])
def test_asset_flags_must_be_bools(field_name: str) -> None:
    values: dict[str, Any] = {"tradable": True, "fractionable": True, "status": "active"}
    values[field_name] = 1
    with pytest.raises(ValueError, match=field_name):
        Asset(**values, cusip=None)


def test_asset_status_and_cusip_must_be_identifiers() -> None:
    with pytest.raises(ValueError, match="status"):
        Asset(True, True, "", None)
    with pytest.raises(ValueError, match="cusip"):
        Asset(True, True, "active", "")


# --- The Broker interface -------------------------------------------------------------


def test_broker_declares_every_method_abstract() -> None:
    assert Broker.__abstractmethods__ == frozenset(
        {
            "submit",
            "cancel",
            "get_order",
            "open_orders",
            "fills",
            "positions",
            "account",
            "assets",
        }
    )


def test_broker_has_no_clock_member() -> None:
    assert not hasattr(Broker, "clock")
    assert not any("clock" in name for name in vars(Broker))


def test_cancel_returns_none_and_fills_takes_an_optional_since() -> None:
    assert inspect.signature(Broker.cancel).return_annotation in (None, "None")
    since = inspect.signature(Broker.fills).parameters["since"]
    assert since.default is None

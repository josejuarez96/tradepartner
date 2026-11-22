"""One `Broker` contract, run against `FakeBroker` and `AlpacaBroker` (plan
T436, #3627).

Almost every execution test runs on the fake, so the fake has to behave like
the adapter wherever the `Broker` ABC states a behaviour. Each case here is one
test body over the `harness` fixture, which is parametrised over both brokers
(`broker_contract.py`), and asserts only what the ABC's docstrings say
(`src/tradepartner/adapters/broker.py`). The Alpaca side is the real adapter
over the real raw client, answered from the recordings in
`tests/fixtures/alpaca/paper/`: no network.

**A difference is a finding, not a fix.** A case that fails on one broker is
marked on that parameter with `pytest.mark.xfail(strict=True, reason="#<issue>")`
and the difference is filed as its own issue; neither broker is changed here.
"""

from __future__ import annotations

import inspect
import sys
from dataclasses import replace
from decimal import Decimal

import pytest
from broker_contract import (
    HARNESSES,
    UNKNOWN_BROKER_ORDER_ID,
    UNKNOWN_CLIENT_ORDER_ID,
    Harness,
)

from tradepartner.adapters.broker import (
    TERMINAL_STATUSES,
    DuplicateClientOrderIdError,
    Fill,
    OrderNotOpenError,
    OrderStatus,
    Side,
    UnknownOrderError,
)

#: The brokers every case runs on, by harness name.
BROKERS = ["fake", "alpaca"]


@pytest.fixture(params=BROKERS)
def harness(request: pytest.FixtureRequest) -> Harness:
    """A fresh broker of each kind, with the means to arrange its book."""
    return HARNESSES[request.param]()


def _net(fills: list[Fill]) -> dict[str, float]:
    """Net signed quantity per symbol of `fills`, exact, zero nets dropped."""
    totals: dict[str, Decimal] = {}
    for fill in fills:
        signed = Decimal(repr(fill.quantity)) * (1 if fill.side is Side.BUY else -1)
        totals[fill.symbol] = totals.get(fill.symbol, Decimal(0)) + signed
    return {symbol: float(total) for symbol, total in totals.items() if total != 0}


# --- submit --------------------------------------------------------------------


def test_a_second_submit_with_the_same_client_order_id_raises_duplicate_and_leaves_the_first_unchanged(  # noqa: E501
    harness: Harness,
) -> None:
    broker = harness.broker
    request = harness.open_order()
    broker.submit(request)
    before = broker.get_order(request.client_order_id)
    assert request.quantity is not None

    with pytest.raises(DuplicateClientOrderIdError):
        broker.submit(request)
    with pytest.raises(DuplicateClientOrderIdError):
        # The id alone decides: another size under the same id is refused too.
        broker.submit(replace(request, quantity=request.quantity + 1))

    assert broker.get_order(request.client_order_id) == before
    assert [o.client_order_id for o in broker.open_orders()] == [request.client_order_id]


# --- cancel --------------------------------------------------------------------


def test_cancel_of_an_unknown_order_raises_unknown_order(harness: Harness) -> None:
    broker = harness.broker
    broker.submit(harness.open_order())

    with pytest.raises(UnknownOrderError):
        broker.cancel(UNKNOWN_CLIENT_ORDER_ID)


def test_cancel_of_a_terminal_order_raises_order_not_open(harness: Harness) -> None:
    broker = harness.broker
    filled, resting = harness.filled_order(), harness.open_order()
    broker.submit(filled)
    broker.submit(resting)
    broker.cancel(resting.client_order_id)
    assert broker.get_order(filled.client_order_id).status is OrderStatus.FILLED
    assert broker.get_order(resting.client_order_id).status is OrderStatus.CANCELLED

    with pytest.raises(OrderNotOpenError):
        broker.cancel(filled.client_order_id)
    with pytest.raises(OrderNotOpenError):
        broker.cancel(resting.client_order_id)

    assert broker.get_order(filled.client_order_id).status is OrderStatus.FILLED


def test_cancel_of_an_open_order_returns_none_and_get_order_then_reads_cancelled(
    harness: Harness,
) -> None:
    broker = harness.broker
    request = harness.open_order()
    broker.submit(request)
    assert broker.get_order(request.client_order_id).status not in TERMINAL_STATUSES

    # Asserted on the value, not by assignment: `cancel` is typed `-> None`.
    assert broker.cancel(request.client_order_id) is None  # type: ignore[func-returns-value]

    assert broker.get_order(request.client_order_id).status is OrderStatus.CANCELLED


# --- reads ---------------------------------------------------------------------


def test_get_order_of_an_unknown_id_raises_unknown_order(harness: Harness) -> None:
    broker = harness.broker
    request = harness.open_order()
    broker.submit(request)
    assert broker.get_order(request.client_order_id).client_order_id == request.client_order_id

    with pytest.raises(UnknownOrderError):
        broker.get_order(UNKNOWN_CLIENT_ORDER_ID)


def test_get_order_by_broker_id_of_an_unknown_id_raises_unknown_order(harness: Harness) -> None:
    broker = harness.broker
    request = harness.filled_order()
    broker.submit(request)
    known = broker.get_order(request.client_order_id)
    assert known.broker_order_id is not None
    # A terminal order is read by the broker's own id like any other (#3335).
    assert broker.get_order_by_broker_id(known.broker_order_id) == known

    with pytest.raises(UnknownOrderError):
        broker.get_order_by_broker_id(UNKNOWN_BROKER_ORDER_ID)


def test_open_orders_holds_no_terminal_order(harness: Harness) -> None:
    broker = harness.broker
    resting, filled = harness.open_order(), harness.filled_order()
    broker.submit(resting)
    broker.submit(filled)
    assert broker.get_order(filled.client_order_id).status is OrderStatus.FILLED

    listed = broker.open_orders()
    assert [o.client_order_id for o in listed] == [resting.client_order_id]
    assert all(o.status not in TERMINAL_STATUSES for o in listed)

    broker.cancel(resting.client_order_id)
    assert broker.open_orders() == []


# --- fills and positions -------------------------------------------------------


def test_fills_come_back_oldest_first(harness: Harness) -> None:
    older, newer = harness.two_fills()

    fills = harness.broker.fills()

    assert [f.client_order_id for f in fills] == [older, newer]
    assert fills[0].filled_at < fills[1].filled_at
    assert harness.broker.fills(None) == fills


def test_fills_since_keeps_a_fill_at_exactly_since_and_drops_earlier_ones(
    harness: Harness,
) -> None:
    older, newer = harness.two_fills()
    first, second = harness.broker.fills()
    assert first.filled_at < second.filled_at

    assert harness.broker.fills(first.filled_at) == [first, second]
    since_the_second = harness.broker.fills(second.filled_at)

    assert since_the_second == [second]
    assert [f.client_order_id for f in since_the_second] == [newer] != [older]


def test_positions_net_the_fills_and_drop_a_zero_net_symbol(harness: Harness) -> None:
    broker = harness.broker
    harness.hold_a_position()
    held = _net(broker.fills())
    assert held and all(quantity > 0 for quantity in held.values())

    positions = broker.positions()
    assert {symbol: p.quantity for symbol, p in positions.items()} == held
    assert all(p.symbol == symbol for symbol, p in positions.items())

    harness.flatten()
    fills = broker.fills()
    assert _net(fills) == {} and {f.symbol for f in fills} >= set(held)
    assert broker.positions() == {}


# --- the suite itself ----------------------------------------------------------


def test_every_contract_case_runs_on_both_brokers() -> None:
    """A later case cannot be written for one broker only: every test in this
    module takes `harness`, and `harness` is both brokers and nothing else."""
    module = sys.modules[__name__]
    cases = {
        name: fn
        for name, fn in inspect.getmembers(module, inspect.isfunction)
        if name.startswith("test_") and fn.__module__ == __name__
    }
    del cases[test_every_contract_case_runs_on_both_brokers.__name__]

    assert len(cases) >= 10
    without = [n for n, fn in cases.items() if "harness" not in inspect.signature(fn).parameters]
    assert without == []
    assert BROKERS == ["fake", "alpaca"] == list(HARNESSES)

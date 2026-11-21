"""Harnesses for the broker contract suite (plan T436, #3627): the two things
`tests/adapters/test_broker_contract.py` needs to run one test body against
`FakeBroker` and against `AlpacaBroker`.

A harness owns a `Broker` and knows how to put it in the few states the
contract cases need. It says nothing about what the broker should then answer:
every expectation lives in the test body, which is the same for both.

**No network, no new recording.** `AlpacaHarness` builds the real adapter over
the real raw client exactly as `_broker()` in `test_alpaca_broker.py` does, with
that module's `Recorded` HTTP adapter mounted on the session. `_PaperAccount`
answers it with the payloads recorded in `tests/fixtures/alpaca/paper/` and
nothing else: it chooses *which* recorded payload answers a request (an order by
its `client_order_id`, the fills of the orders submitted so far, the positions
recording that belongs to that set of orders) and refuses, loudly, a request the
recordings cannot answer. It never builds or edits an order, fill or position
payload, so a request must be one the recording session sent.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

import requests
from test_alpaca_broker import NOW, Recorded, _broker, _by_client_id, _by_id, _load

from tradepartner.adapters.broker import Broker, OrderRequest, Side
from tradepartner.adapters.fake_broker import FakeBroker, FillAt

#: An id neither broker has ever been sent, and a broker order id neither issued
#: (the fake numbers its own; Alpaca's are UUIDs).
UNKNOWN_CLIENT_ORDER_ID = "never-sent"
UNKNOWN_BROKER_ORDER_ID = "00000000-0000-0000-0000-000000000000"


class Harness(Protocol):
    """What a contract case may ask of the broker under test."""

    broker: Broker

    def open_order(self) -> OrderRequest:
        """A request the broker will hold open (accepted, unfilled)."""

    def filled_order(self) -> OrderRequest:
        """A request the broker will have filled when read back."""

    def two_fills(self) -> tuple[str, str]:
        """Arrange two fills at different instants on a fresh book; return
        their `client_order_id`s, oldest first."""

    def hold_a_position(self) -> None:
        """Arrange, on a fresh book, buys and a smaller sell of one symbol, so
        its net quantity is positive."""

    def flatten(self) -> None:
        """After `hold_a_position`: arrange the fills that take every symbol
        with a fill to a net of zero."""


# --- FakeBroker ---------------------------------------------------------------


class _SteppedClock:
    """The fake's clock: a fixed instant the harness moves by hand, so two
    fills can be given two different times."""

    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class FakeHarness:
    """`FakeBroker` as `tests/test_cli_paper.py` builds it (`auto_fill=False`,
    so an unscripted order rests), filled through `script(FillAt())`."""

    def __init__(self) -> None:
        self._clock = _SteppedClock()
        self._fake = FakeBroker(
            assets=FakeBroker.active_assets("KO"),
            clock=self._clock,
            price_of=lambda _s: 100.0,
            auto_fill=False,
            account_id="PA1",
        )
        self.broker: Broker = self._fake

    def open_order(self) -> OrderRequest:
        return OrderRequest(
            client_order_id="contract-open", symbol="KO", side=Side.BUY, quantity=1.0
        )

    def filled_order(self) -> OrderRequest:
        return self._filling("contract-filled", Side.BUY, 1.0)

    def two_fills(self) -> tuple[str, str]:
        self.broker.submit(self._filling("contract-fill-1", Side.BUY, 1.0))
        self._clock.now += timedelta(seconds=1)
        self.broker.submit(self._filling("contract-fill-2", Side.BUY, 1.0))
        return "contract-fill-1", "contract-fill-2"

    def hold_a_position(self) -> None:
        self.broker.submit(self._filling("contract-buy-1", Side.BUY, 1.0))
        self.broker.submit(self._filling("contract-buy-2", Side.BUY, 0.75))
        self.broker.submit(self._filling("contract-sell-1", Side.SELL, 0.5))

    def flatten(self) -> None:
        self.broker.submit(self._filling("contract-sell-2", Side.SELL, 1.25))

    def _filling(self, client_order_id: str, side: Side, quantity: float) -> OrderRequest:
        """A request scripted to fill in full when it is submitted."""
        self._fake.script(FillAt(), client_order_id=client_order_id)
        return OrderRequest(
            client_order_id=client_order_id, symbol="KO", side=side, quantity=quantity
        )


# --- AlpacaBroker ---------------------------------------------------------------

#: Alpaca's order statuses that end an order (`STATUS_TABLE`'s terminal rows).
_ALPACA_TERMINAL = frozenset({"filled", "canceled", "expired", "rejected"})
#: The positions recording taken after each set of filled orders, by the last
#: digit of their ids: `positions_held` after the first three fills
#: (0.057436865 + 1 - 0.5 KO), `positions_after` after all six (KO and OCGN flat).
_POSITIONS = {"123": "positions_held", "123689": "positions_after"}


def _recorded_session() -> dict[str, dict[str, Any]]:
    """Every order the recording session placed, by `client_order_id`: its
    `submit` response and its `polls`."""
    names = ("buy_fractional", "buy_whole", "sell_fractional", "resting", "non_fractionable_buy")
    orders = [_load(name) for name in names] + _load("flatten")
    return {order["submit"]["client_order_id"]: order for order in orders}


def _same_size(sent: Any, recorded: Any) -> bool:
    if sent is None or recorded is None:
        return sent is None and recorded is None
    return float(sent) == float(recorded)


class _PaperAccount:
    """The recorded paper account, as far as the recordings go (module
    docstring). `orders` is the book: the current recorded payload of every
    order submitted so far, by `client_order_id`."""

    def __init__(self, http: Recorded) -> None:
        self.session = _recorded_session()
        self.orders: dict[str, dict[str, Any]] = {}
        self._by_broker_id: dict[str, dict[str, Any]] = {}
        http.route("POST", "/v2/orders", self._submit)
        http.route("GET", "/v2/orders:by_client_order_id", _by_client_id(self.orders))
        http.route("GET", "/v2/orders/[^/]+", _by_id(self._by_broker_id))
        http.route("DELETE", "/v2/orders/[^/]+", self._cancel)
        http.route("GET", "/v2/orders", self._list_orders)
        http.route("GET", "/v2/account/activities/FILL", self._fill_activities)
        http.route("GET", "/v2/positions", self._positions)

    def request_for(self, digit: str) -> OrderRequest:
        """The `OrderRequest` the recording session sent for the order whose
        `client_order_id` ends in `-<digit>`."""
        (payload,) = (o["submit"] for cid, o in self.session.items() if cid.endswith(f"-{digit}"))
        return OrderRequest(
            client_order_id=payload["client_order_id"],
            symbol=payload["symbol"],
            side=Side(payload["side"]),
            notional=None if payload["notional"] is None else float(payload["notional"]),
            quantity=None if payload["qty"] is None else float(payload["qty"]),
            position_intent=payload["position_intent"],
        )

    def _store(self, payload: dict[str, Any]) -> None:
        self.orders[payload["client_order_id"]] = payload
        self._by_broker_id[payload["id"]] = payload

    def _filled(self) -> list[dict[str, Any]]:
        return [o for o in self.orders.values() if o["status"] == "filled"]

    def _submit(self, request: requests.PreparedRequest) -> tuple[int, Any]:
        """Answer the recorded `submit` response and book the order's last
        recorded poll; a repeated id gets the recorded duplicate refusal."""
        body = json.loads(request.body or b"{}")
        if body["client_order_id"] in self.orders:
            refusal = _load("duplicate_client_order_id")["error"]
            return refusal["status_code"], json.loads(refusal["body"])
        recorded = self.session.get(body["client_order_id"], {}).get("submit")
        if recorded is None or not (
            body["symbol"] == recorded["symbol"]
            and body["side"] == recorded["side"]
            and _same_size(body.get("qty"), recorded["qty"])
            and _same_size(body.get("notional"), recorded["notional"])
        ):
            return 500, {"message": f"no recording answers this order: {body!r}"}
        self._store(self.session[body["client_order_id"]]["polls"][-1])
        return 200, recorded

    def _cancel(self, request: requests.PreparedRequest) -> tuple[int, Any]:
        """Swap in the one recorded cancelled payload (`resting_cancelled`)."""
        broker_order_id = urlsplit(request.url or "").path.rsplit("/", 1)[1]
        (cancelled,) = _load("resting_cancelled")
        current = self._by_broker_id.get(broker_order_id)
        if current is None or current["status"] in _ALPACA_TERMINAL:
            return 404, {"code": 40410000, "message": "order not found"}
        if cancelled["id"] != broker_order_id:
            return 500, {"message": f"no recorded cancel of order {broker_order_id!r}"}
        self._store(cancelled)
        return 204, None

    def _list_orders(self, request: requests.PreparedRequest) -> tuple[int, Any]:
        query = parse_qs(urlsplit(request.url or "").query)
        if query.get("status") != ["open"]:
            return 500, {"message": f"no recording answers this order list: {query!r}"}
        return 200, [o for o in self.orders.values() if o["status"] not in _ALPACA_TERMINAL]

    def _fill_activities(self, request: requests.PreparedRequest) -> tuple[int, Any]:
        """The recorded fills of the filled orders on the book, strictly after
        the request's `after`, as Alpaca's own `after` is."""
        after = datetime.fromisoformat(parse_qs(urlsplit(request.url or "").query)["after"][0])
        filled = {order["id"] for order in self._filled()}
        return 200, [
            row
            for row in _load("fill_activities")
            if row["order_id"] in filled and datetime.fromisoformat(row["transaction_time"]) > after
        ]

    def _positions(self, _request: requests.PreparedRequest) -> tuple[int, Any]:
        digits = "".join(sorted(order["client_order_id"][-1] for order in self._filled()))
        if digits not in _POSITIONS:
            return 500, {"message": f"no positions recording for filled orders {digits!r}"}
        return 200, _load(_POSITIONS[digits])


class AlpacaHarness:
    """`AlpacaBroker` over `AlpacaTradingRaw`, answered by `_PaperAccount`."""

    def __init__(self) -> None:
        self.broker, self.http = _broker()
        self._account = _PaperAccount(self.http)

    def open_order(self) -> OrderRequest:
        return self._account.request_for("5")  # resting.json

    def filled_order(self) -> OrderRequest:
        return self._account.request_for("2")  # buy_whole.json

    def two_fills(self) -> tuple[str, str]:
        first, second = self._account.request_for("1"), self._account.request_for("2")
        self.broker.submit(first)
        self.broker.submit(second)
        return first.client_order_id, second.client_order_id

    def hold_a_position(self) -> None:
        # buy_fractional, buy_whole, sell_fractional: the book `positions_held` shows.
        for digit in ("1", "2", "3"):
            self.broker.submit(self._account.request_for(digit))

    def flatten(self) -> None:
        # non_fractionable_buy, then both orders of flatten.json: `positions_after`.
        for digit in ("6", "8", "9"):
            self.broker.submit(self._account.request_for(digit))


HARNESSES: dict[str, type[FakeHarness] | type[AlpacaHarness]] = {
    "fake": FakeHarness,
    "alpaca": AlpacaHarness,
}

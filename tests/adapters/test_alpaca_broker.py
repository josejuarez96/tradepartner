"""`AlpacaBroker` on T48b's recorded paper responses (plan T48c; spec
"Interface and adapter" criteria). No network: the real paper `TradingClient`
is built, and a fake HTTP adapter mounted on its session serves the recordings
in `tests/fixtures/alpaca/paper/` and records every request it is sent.
"""

from __future__ import annotations

import ast
import copy
import inspect
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from requests.adapters import HTTPAdapter

from tradepartner.adapters import alpaca_broker as broker_mod
from tradepartner.adapters.alpaca_broker import (
    STATUS_TABLE,
    AlpacaBroker,
    AlpacaBrokerConfigError,
    symbol_for,
    ticker_for,
)
from tradepartner.adapters.alpaca_trading_raw import (
    AlpacaPaperCredentialsError,
    AlpacaTradingError,
    AlpacaTradingRaw,
)
from tradepartner.adapters.broker import (
    Account,
    Asset,
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
from tradepartner.config import Settings
from tradepartner.errors import ClockError, SystemFaultError

PAPER = Path(__file__).resolve().parents[1] / "fixtures" / "alpaca" / "paper"
PAPER_URL = "https://paper-api.alpaca.markets/v2/"
PAPER_KEY = "PKPAPERFAKE1234567890"  # gitleaks:allow
PAPER_SECRET = "paperSecretFake1234567890abcdefghij"  # gitleaks:allow
DATA_KEY = "AKDATAFAKE1234567890"  # gitleaks:allow
DATA_SECRET = "dataSecretFake1234567890abcdef"  # gitleaks:allow
NOW = datetime(2026, 10, 8, 15, 11, tzinfo=UTC)


def _load(name: str) -> Any:
    return json.loads((PAPER / f"{name}.json").read_text())


def _settings(keys: dict[str, Any] | None = None, **alpaca: Any) -> Settings:
    values: dict[str, Any] = {
        "alpaca_paper_api_key": PAPER_KEY,
        "alpaca_paper_api_secret": PAPER_SECRET,
        "alpaca_api_key": DATA_KEY,
        "alpaca_api_secret": DATA_SECRET,
    }
    values.update(keys or {})
    return Settings(_env_file=None, alpaca=alpaca, **values)


def _recorded_orders() -> list[dict[str, Any]]:
    """Every order payload in the recordings, in file order."""
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "client_order_id" in node and "status" in node:
                found.append(node)
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for path in sorted(PAPER.glob("*.json")):
        walk(json.loads(path.read_text()))
    return found


def _final_orders() -> dict[str, dict[str, Any]]:
    """The last recorded payload of each broker order id."""
    return {order["id"]: order for order in _recorded_orders()}


class FakeClock:
    """Pacing clock: nothing sleeps for real."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


Responder = Callable[[requests.PreparedRequest], tuple[int, Any]]


class Recorded(HTTPAdapter):
    """Serves routed responses and records every request sent."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[requests.PreparedRequest] = []
        self.routes: list[tuple[str, re.Pattern[str], Responder]] = []

    def route(self, method: str, path: str, responder: Responder | tuple[int, Any]) -> None:
        fn = responder if callable(responder) else (lambda _r, out=responder: out)
        self.routes.insert(0, (method, re.compile(path), fn))

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        self.sent.append(request)
        path = urlsplit(request.url or "").path
        for method, pattern, fn in self.routes:
            if request.method == method and pattern.fullmatch(path):
                status, body = fn(request)
                break
        else:
            status, body = 404, {"code": 40410000, "message": "not routed"}
        response = requests.Response()
        response.status_code = status
        response._content = b"" if body is None else json.dumps(body).encode()
        response.headers["Content-Type"] = "application/json"
        response.url = request.url or ""
        response.request = request
        return response

    def calls(self, method: str, path: str) -> list[requests.PreparedRequest]:
        return [
            r
            for r in self.sent
            if r.method == method and re.fullmatch(path, urlsplit(r.url or "").path)
        ]


def _broker(
    clock: Callable[[], Any] = lambda: NOW, settings: Settings | None = None
) -> tuple[AlpacaBroker, Recorded]:
    settings = settings or _settings()
    raw = AlpacaTradingRaw(settings, clock=FakeClock())
    http = Recorded()
    raw._client._session.mount("https://", http)  # type: ignore[attr-defined]
    return AlpacaBroker(settings, clock, client=raw), http


def _by_client_id(orders: dict[str, Any]) -> Responder:
    """`GET /v2/orders:by_client_order_id` over `orders` (404 when absent)."""

    def respond(request: requests.PreparedRequest) -> tuple[int, Any]:
        coid = parse_qs(urlsplit(request.url or "").query)["client_order_id"][0]
        if coid in orders:
            return 200, orders[coid]
        return 404, {"code": 40410000, "message": "order not found"}

    return respond


def _by_id(orders: dict[str, Any]) -> Responder:
    def respond(request: requests.PreparedRequest) -> tuple[int, Any]:
        order_id = urlsplit(request.url or "").path.rsplit("/", 1)[1]
        if order_id in orders:
            return 200, orders[order_id]
        return 404, {"code": 40410000, "message": "order not found"}

    return respond


def _request(**overrides: Any) -> OrderRequest:
    values: dict[str, Any] = {
        "client_order_id": "rec20261008151015-1",
        "symbol": "KO",
        "side": Side.BUY,
        "notional": 5.0,
    }
    values.update(overrides)
    return OrderRequest(**values)


# --- status table ------------------------------------------------------------


def test_the_status_table_is_exactly_req_2() -> None:
    assert dict(STATUS_TABLE) == {
        "new": OrderStatus.ACCEPTED,
        "accepted": OrderStatus.ACCEPTED,
        "pending_new": OrderStatus.ACCEPTED,
        "pending_cancel": OrderStatus.ACCEPTED,
        "partially_filled": OrderStatus.ACCEPTED,
        "filled": OrderStatus.FILLED,
        "expired": OrderStatus.EXPIRED,
        "rejected": OrderStatus.REJECTED,
        "canceled": OrderStatus.CANCELLED,
    }


@pytest.mark.parametrize(
    "payload", _recorded_orders(), ids=lambda p: f"{p['client_order_id']}-{p['status']}"
)
def test_each_recorded_status_maps_per_the_table(payload: dict[str, Any]) -> None:
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", (200, payload))
    order = broker.get_order(payload["client_order_id"])
    assert order.status is STATUS_TABLE[payload["status"]]
    assert order.broker_order_id == payload["id"]


def test_the_recordings_cover_these_statuses() -> None:
    assert {o["status"] for o in _recorded_orders()} == {
        "pending_new",
        "new",
        "filled",
        "canceled",
    }


def test_a_recorded_filled_notional_order_maps_every_field() -> None:
    payload = _load("buy_fractional")["polls"][-1]
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", (200, payload))

    assert broker.get_order("rec20261008151015-1") == Order(
        client_order_id="rec20261008151015-1",
        symbol="KO",
        side=Side.BUY,
        notional=5.0,
        quantity=None,
        status=OrderStatus.FILLED,
        submitted_at=datetime(2026, 10, 8, 15, 10, 18, 94153, tzinfo=UTC),
        broker_order_id="f1a08ee3-8b15-4d31-b04b-944f30a06604",
        filled_quantity=0.057436865,
        filled_avg_price=86.878,
        filled_at=datetime(2026, 10, 8, 15, 10, 18, 97464, tzinfo=UTC),
        order_type="market",
        time_in_force="day",
        limit_price=None,
        stop_price=None,
        asset_class="us_equity",
        order_class="simple",
        legs=(),
    )


def test_a_recorded_resting_limit_order_maps_every_field() -> None:
    payload = _load("resting")["polls"][-1]
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", (200, payload))

    assert broker.get_order("rec20261008151015-5") == Order(
        client_order_id="rec20261008151015-5",
        symbol="KO",
        side=Side.BUY,
        notional=None,
        quantity=1.0,
        status=OrderStatus.ACCEPTED,
        submitted_at=datetime(2026, 10, 8, 15, 10, 21, 375005, tzinfo=UTC),
        broker_order_id="ad78ddf9-eadd-49e7-b451-eebfcf25ff04",
        filled_quantity=None,
        filled_avg_price=None,
        filled_at=None,
        order_type="limit",
        time_in_force="day",
        limit_price=78.19,
        stop_price=None,
        asset_class="us_equity",
        order_class="simple",
        legs=(),
    )


def test_alpacas_empty_order_class_is_simple_and_any_other_class_is_kept() -> None:
    payload = _load("resting")["polls"][-1]
    assert payload["order_class"] == ""
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", (200, payload))
    assert broker.get_order("rec20261008151015-5").order_class == "simple"

    bracket = {**payload, "order_class": "bracket", "legs": [dict(payload, legs=None)]}
    http.route("GET", "/v2/orders:by_client_order_id", (200, bracket))
    order = broker.get_order("rec20261008151015-5")
    assert order.order_class == "bracket"
    assert len(order.legs) == 1 and order.legs[0].order_class == "simple"


def test_partially_filled_is_accepted_with_its_fills() -> None:
    payload = dict(
        _load("resting")["polls"][-1],
        status="partially_filled",
        filled_qty="0.4",
        filled_avg_price="78.19",
    )
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", (200, payload))
    order = broker.get_order("rec20261008151015-5")
    assert order.status is OrderStatus.ACCEPTED
    assert (order.filled_quantity, order.filled_avg_price, order.filled_at) == (0.4, 78.19, None)


@pytest.mark.parametrize("status", ["held", "pending_replace", "done_for_day", "", None, "FILLED"])
def test_a_status_absent_from_the_table_raises_system_fault(status: Any) -> None:
    payload = dict(_load("buy_fractional")["polls"][-1], status=status)
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", (200, payload))
    http.route("GET", "/v2/orders", (200, [payload]))
    with pytest.raises(SystemFaultError, match="status table"):
        broker.get_order("rec20261008151015-1")
    with pytest.raises(SystemFaultError, match="status table"):
        broker.open_orders()


def test_a_malformed_order_payload_raises_system_fault() -> None:
    payload = copy.deepcopy(_load("buy_fractional")["polls"][-1])
    del payload["submitted_at"]
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", (200, payload))
    with pytest.raises(SystemFaultError, match="malformed alpaca order"):
        broker.get_order("rec20261008151015-1")


# --- transport: retries and pacing ------------------------------------------


class CountingClient:
    """A fake SDK client whose every call times out, counting calls."""

    def __init__(self) -> None:
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        def call(*_args: Any, **_kwargs: Any) -> Any:
            self.calls += 1
            raise requests.Timeout("slow")

        return call


def test_a_transport_timeout_is_retried_max_retries_times_then_raised() -> None:
    settings = _settings(trading_max_retries=2)
    client = CountingClient()
    raw = AlpacaTradingRaw(settings, client=client, clock=FakeClock())  # type: ignore[arg-type]
    broker = AlpacaBroker(settings, lambda: NOW, client=raw)

    with pytest.raises(AlpacaTradingError) as err:
        broker.account()
    assert client.calls == 3
    for secret in (PAPER_KEY, PAPER_SECRET, DATA_KEY, DATA_SECRET):
        assert secret not in str(err.value)


def test_requests_are_paced_at_the_configured_rate_on_a_fake_clock() -> None:
    settings = _settings(trading_requests_per_minute=30)
    pacing = FakeClock()
    raw = AlpacaTradingRaw(settings, clock=pacing)
    http = Recorded()
    raw._client._session.mount("https://", http)  # type: ignore[attr-defined]
    http.route("GET", "/v2/account", (200, _load("account_before")))
    http.route("GET", "/v2/positions", (200, _load("positions_held")))
    broker = AlpacaBroker(settings, lambda: NOW, client=raw)

    broker.account()
    broker.positions()
    pacing.now += 0.5
    broker.account()

    assert len(http.sent) == 3
    assert pacing.sleeps == [pytest.approx(2.0), pytest.approx(1.5)]


# --- paper only, credentials, construction ----------------------------------


def _route_everything(http: Recorded) -> None:
    orders = {o["client_order_id"]: o for o in _recorded_orders()}
    del orders["rec20261008151015-1"]
    orders["rec20261008151015-5"] = _load("resting")["polls"][-1]
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id(orders))
    http.route("GET", "/v2/orders/[^/]+", _by_id(_final_orders()))
    http.route("POST", "/v2/orders", (200, _load("buy_fractional")["submit"]))
    http.route("DELETE", "/v2/orders/[^/]+", (204, None))
    http.route("GET", "/v2/orders", (200, _load("open_orders_after")))
    http.route("GET", "/v2/positions", (200, _load("positions_held")))
    http.route("GET", "/v2/account", (200, _load("account_after")))
    http.route("GET", "/v2/assets/KO", (200, _load("assets")[0]))
    http.route("GET", "/v2/account/activities/FILL", (200, _load("fill_activities")))


def test_the_base_url_of_every_request_is_the_paper_endpoint() -> None:
    broker, http = _broker()
    _route_everything(http)

    broker.submit(_request())
    broker.cancel("rec20261008151015-5")
    broker.get_order("rec20261008151015-5")
    broker.open_orders()
    broker.fills()
    broker.positions()
    broker.account()
    broker.assets(["KO"])

    methods = {r.method for r in http.sent}
    assert methods == {"GET", "POST", "DELETE"}
    assert len(http.sent) >= 9
    for request in http.sent:
        assert (request.url or "").startswith(PAPER_URL), request.url


def test_a_missing_paper_key_raises_a_credentials_error_with_no_key_in_it() -> None:
    settings = _settings({"alpaca_paper_api_key": None})
    with pytest.raises(AlpacaPaperCredentialsError) as err:
        AlpacaBroker(settings, lambda: NOW)
    text = str(err.value)
    assert "ALPACA_PAPER_API_KEY" in text
    for secret in (PAPER_SECRET, DATA_KEY, DATA_SECRET):
        assert secret not in text


@pytest.mark.parametrize("unset", ["quantity_decimals", "client_order_id_max_length"])
def test_construction_is_refused_while_a_broker_fact_is_unset(unset: str) -> None:
    settings = _settings(**{unset: None})
    raw = AlpacaTradingRaw(_settings(), client=CountingClient(), clock=FakeClock())  # type: ignore[arg-type]
    with pytest.raises(AlpacaBrokerConfigError, match=f"alpaca.{unset}"):
        AlpacaBroker(settings, lambda: NOW, client=raw)
    with pytest.raises(AlpacaBrokerConfigError):
        AlpacaBroker(settings, lambda: NOW)


def test_the_concrete_class_exposes_its_clock() -> None:
    def clock() -> datetime:
        return NOW

    broker, _ = _broker(clock)
    assert broker.clock is clock


def test_the_adapter_imports_nothing_from_execution() -> None:
    tree = ast.parse(inspect.getsource(broker_mod))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append("." * node.level + (node.module or ""))
    assert imported
    assert not [m for m in imported if m.startswith(("tradepartner.execution", "."))]


# --- clock -------------------------------------------------------------------


def _naive() -> datetime:
    return datetime(2026, 10, 8, 15, 11)  # noqa: DTZ001


def _raises() -> datetime:
    raise RuntimeError("clock down")


def _not_a_datetime() -> Any:
    return "2026-10-08T15:11:00Z"


def _overflow() -> datetime:
    return datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=5)))


CALLS: dict[str, Callable[[AlpacaBroker], object]] = {
    "submit": lambda b: b.submit(_request()),
    "cancel": lambda b: b.cancel("rec20261008151015-5"),
    "get_order": lambda b: b.get_order("rec20261008151015-5"),
    "open_orders": lambda b: b.open_orders(),
    "fills": lambda b: b.fills(),
    "positions": lambda b: b.positions(),
    "account": lambda b: b.account(),
    "assets": lambda b: b.assets(["KO"]),
}


@pytest.mark.parametrize("clock", [_naive, _raises, _not_a_datetime, _overflow])
@pytest.mark.parametrize("call", list(CALLS), ids=str)
def test_a_bad_clock_raises_clock_error_before_any_request(
    clock: Callable[[], Any], call: str
) -> None:
    broker, http = _broker(clock)
    _route_everything(http)
    with pytest.raises(ClockError):
        CALLS[call](broker)
    assert http.sent == []


# --- submit and dedupe ---------------------------------------------------------


def test_submit_sends_a_market_day_order_and_maps_the_response() -> None:
    recorded = _load("buy_fractional")
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id({}))
    http.route("POST", "/v2/orders", (200, recorded["submit"]))

    order = broker.submit(_request())

    (post,) = http.calls("POST", "/v2/orders")
    body = json.loads(post.body or b"{}")
    assert body == {
        "symbol": "KO",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "client_order_id": "rec20261008151015-1",
        "notional": 5.0,
    }
    assert order.status is OrderStatus.ACCEPTED and order.filled_quantity is None
    assert order.broker_order_id == "f1a08ee3-8b15-4d31-b04b-944f30a06604"
    # The dedupe read came first.
    assert [r.method for r in http.sent] == ["GET", "POST"]


def test_a_quantity_sell_sends_its_quantity_at_nine_decimals() -> None:
    sell = _load("flatten")[0]
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id({}))
    http.route("POST", "/v2/orders", (200, sell["submit"]))

    order = broker.submit(
        _request(
            client_order_id=sell["submit"]["client_order_id"],
            side=Side.SELL,
            notional=None,
            quantity=0.557436865,
        )
    )

    body = json.loads(http.calls("POST", "/v2/orders")[0].body or b"{}")
    assert body["qty"] == 0.557436865 and "notional" not in body
    assert order.quantity == 0.557436865 and order.side is Side.SELL


def test_a_duplicate_id_found_through_get_order_raises_before_any_submit() -> None:
    existing = _load("buy_fractional")["polls"][-1]
    broker, http = _broker()
    http.route(
        "GET",
        "/v2/orders:by_client_order_id",
        _by_client_id({existing["client_order_id"]: existing}),
    )
    http.route("POST", "/v2/orders", (200, _load("buy_fractional")["submit"]))

    with pytest.raises(DuplicateClientOrderIdError):
        broker.submit(_request())
    assert http.calls("POST", "/v2/orders") == []


def test_the_brokers_recorded_duplicate_refusal_raises_duplicate() -> None:
    error = _load("duplicate_client_order_id")["error"]
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id({}))
    http.route("POST", "/v2/orders", (error["status_code"], json.loads(error["body"])))

    with pytest.raises(DuplicateClientOrderIdError):
        broker.submit(_request())


@pytest.mark.parametrize("name", ["sell_above_held", "non_fractionable_sell_fractional"])
def test_a_recorded_submit_refusal_is_raised_with_no_key_in_it(name: str) -> None:
    error = _load(name)["error"]
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id({}))
    http.route("POST", "/v2/orders", (error["status_code"], json.loads(error["body"])))

    with pytest.raises(AlpacaTradingError) as err:
        broker.submit(_request(side=Side.SELL, notional=None, quantity=0.5))
    assert err.value.status_code == 403
    assert not isinstance(err.value, DuplicateClientOrderIdError)
    text = str(err.value) + repr(vars(err.value))
    for secret in (PAPER_KEY, PAPER_SECRET, DATA_KEY, DATA_SECRET):
        assert secret not in text


def test_an_id_above_the_length_limit_is_refused_before_any_request() -> None:
    broker, http = _broker(settings=_settings(client_order_id_max_length=10))
    with pytest.raises(ValueError, match="above 10"):
        broker.submit(_request(client_order_id="x" * 11))
    assert http.sent == []


def test_a_quantity_beyond_the_broker_precision_is_refused_before_any_request() -> None:
    broker, http = _broker(settings=_settings(quantity_decimals=2))
    with pytest.raises(ValueError, match="more than 2 decimals"):
        broker.submit(_request(notional=None, quantity=0.125))
    assert http.sent == []


def test_a_submit_answered_with_another_order_raises_system_fault() -> None:
    other = dict(_load("buy_fractional")["submit"], client_order_id="someone-else")
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id({}))
    http.route("POST", "/v2/orders", (200, other))
    with pytest.raises(SystemFaultError):
        broker.submit(_request())


# --- cancel and get_order -------------------------------------------------------


def test_cancel_returns_none_and_the_outcome_comes_from_get_order() -> None:
    resting = _load("resting")["polls"][-1]
    cancelled = _load("resting_cancelled")[0]
    state = {"rec20261008151015-5": resting}
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id(state))

    def delete(_request: requests.PreparedRequest) -> tuple[int, Any]:
        state["rec20261008151015-5"] = cancelled
        return 204, None

    http.route("DELETE", "/v2/orders/[^/]+", delete)

    assert broker.cancel("rec20261008151015-5") is None
    (sent,) = http.calls("DELETE", "/v2/orders/[^/]+")
    assert (sent.url or "").endswith("/orders/ad78ddf9-eadd-49e7-b451-eebfcf25ff04")
    assert broker.get_order("rec20261008151015-5").status is OrderStatus.CANCELLED


def test_cancel_of_a_terminal_order_raises_with_no_delete() -> None:
    broker, http = _broker()
    http.route(
        "GET",
        "/v2/orders:by_client_order_id",
        (200, _load("resting_cancelled")[0]),
    )
    with pytest.raises(OrderNotOpenError):
        broker.cancel("rec20261008151015-5")
    assert http.calls("DELETE", "/v2/orders/[^/]+") == []


def test_get_order_of_an_unknown_id_raises_unknown_order() -> None:
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id({}))
    with pytest.raises(UnknownOrderError):
        broker.get_order("never-sent")
    with pytest.raises(UnknownOrderError):
        broker.cancel("never-sent")


def test_open_orders_map_the_recorded_lists() -> None:
    broker, http = _broker()
    http.route("GET", "/v2/orders", (200, _load("open_orders_after")))
    assert broker.open_orders() == []
    resting = _load("resting")["polls"][-1]
    http.route("GET", "/v2/orders", (200, [resting]))
    (order,) = broker.open_orders()
    assert order.client_order_id == "rec20261008151015-5"
    assert order.status is OrderStatus.ACCEPTED
    (sent, _) = http.calls("GET", "/v2/orders")
    assert parse_qs(urlsplit(sent.url or "").query)["status"] == ["open"]


# --- fills -----------------------------------------------------------------------


def _fills_broker() -> tuple[AlpacaBroker, Recorded]:
    broker, http = _broker()
    http.route("GET", "/v2/account/activities/FILL", (200, _load("fill_activities")))
    http.route("GET", "/v2/orders/[^/]+", _by_id(_final_orders()))
    return broker, http


def test_recorded_fills_map_every_field_with_the_client_order_id_resolved() -> None:
    broker, http = _fills_broker()

    fills = broker.fills()

    assert fills[0] == Fill(
        client_order_id="rec20261008151015-1",
        symbol="KO",
        side=Side.BUY,
        quantity=0.057436865,
        price=86.878,
        filled_at=datetime(2026, 10, 8, 15, 10, 18, 97465, tzinfo=UTC),
        broker_fill_id="20261008111018097::fc1f73e1-a5c2-4d43-81ec-fb503c42ca5c",
        fee=None,
    )
    activities = _load("fill_activities")
    finals = _final_orders()
    assert [f.client_order_id for f in fills] == [
        finals[a["order_id"]]["client_order_id"] for a in activities
    ]
    assert [f.broker_fill_id for f in fills] == [a["id"] for a in activities]
    assert len(http.calls("GET", "/v2/orders/[^/]+")) == len({a["order_id"] for a in activities})
    (read,) = http.calls("GET", "/v2/account/activities/FILL")
    assert parse_qs(urlsplit(read.url or "").query)["after"] == ["1970-01-01T00:00:00+00:00"]


def test_each_distinct_order_id_is_resolved_once_per_call() -> None:
    activities = _load("fill_activities")
    doubled = [activities[0], dict(activities[0], id="second-fill-of-order-1")]
    broker, http = _broker()
    http.route("GET", "/v2/account/activities/FILL", (200, doubled))
    http.route("GET", "/v2/orders/[^/]+", _by_id(_final_orders()))

    fills = broker.fills()

    assert [f.client_order_id for f in fills] == ["rec20261008151015-1"] * 2
    assert len(http.calls("GET", "/v2/orders/[^/]+")) == 1


def test_fills_since_keep_only_fills_at_or_after_it() -> None:
    broker, http = _fills_broker()
    since = datetime(2026, 10, 8, 15, 10, 19, 730663, tzinfo=UTC)  # the third fill, exactly

    fills = broker.fills(since)

    assert [f.broker_fill_id for f in fills] == [a["id"] for a in _load("fill_activities")[2:]]
    (read,) = http.calls("GET", "/v2/account/activities/FILL")
    after = parse_qs(urlsplit(read.url or "").query)["after"][0]
    assert datetime.fromisoformat(after) == since - timedelta(seconds=1)


def test_a_fill_whose_order_cannot_be_resolved_raises_system_fault() -> None:
    broker, http = _broker()
    http.route("GET", "/v2/account/activities/FILL", (200, _load("fill_activities")))
    http.route("GET", "/v2/orders/[^/]+", _by_id({}))
    with pytest.raises(SystemFaultError, match="no such order"):
        broker.fills()

    unnamed = {k: dict(v, client_order_id=None) for k, v in _final_orders().items()}
    http.route("GET", "/v2/orders/[^/]+", _by_id(unnamed))
    with pytest.raises(SystemFaultError, match="no client_order_id"):
        broker.fills()


# --- account, positions, assets --------------------------------------------------


def test_the_recorded_account_maps_every_field() -> None:
    broker, http = _broker()
    http.route("GET", "/v2/account", (200, _load("account_before")))
    assert broker.account() == Account(
        account_id="<scrubbed>",
        cash=100005.83,
        buying_power=400023.32,
        equity=100005.83,
        as_of=NOW,
        short_market_value=0.0,
        maintenance_margin=0.0,
        daytrade_count=None,
    )
    http.route("GET", "/v2/account", (200, dict(_load("account_after"), daytrade_count=2)))
    account = broker.account()
    assert account.cash == 100005.82 and account.daytrade_count == 2


def test_the_recorded_positions_map() -> None:
    broker, http = _broker()
    http.route("GET", "/v2/positions", (200, _load("positions_held")))
    assert broker.positions() == {"KO": Position(symbol="KO", quantity=0.557436865)}
    http.route("GET", "/v2/positions", (200, _load("positions_after")))
    assert broker.positions() == {}


def test_a_position_whose_side_disagrees_with_its_quantity_raises() -> None:
    broker, http = _broker()
    held = dict(_load("positions_held")[0], side="short")
    http.route("GET", "/v2/positions", (200, [held]))
    with pytest.raises(SystemFaultError, match="malformed alpaca position"):
        broker.positions()


def test_the_recorded_assets_map_every_field() -> None:
    ko, ocgn = _load("assets")
    broker, http = _broker()
    http.route("GET", "/v2/assets/KO", (200, ko))
    http.route("GET", "/v2/assets/OCGN", (200, ocgn))

    assert broker.assets(["ko", "OCGN"]) == {
        "KO": Asset(
            tradable=True,
            fractionable=True,
            status="active",
            cusip=None,
            shortable=True,
            easy_to_borrow=True,
            marginable=True,
        ),
        "OCGN": Asset(
            tradable=True,
            fractionable=False,
            status="active",
            cusip=None,
            shortable=True,
            easy_to_borrow=True,
            marginable=True,
        ),
    }


def test_an_asset_answered_for_another_symbol_raises() -> None:
    broker, http = _broker()
    http.route("GET", "/v2/assets/KO", (200, _load("assets")[1]))
    with pytest.raises(SystemFaultError):
        broker.assets(["KO"])


# --- symbols ---------------------------------------------------------------------

CLASS_SYMBOLS = {"BFB": "BF.B"}
LISTINGS = [
    {"ticker": "BRK-B", "exchange": "NYSE"},
    {"ticker": "BFB", "exchange": "NYSE"},
    {"ticker": "KO", "exchange": "NYSE"},
    {"ticker": "SENEB", "exchange": "NASDAQ"},
]


@pytest.mark.parametrize(
    ("ticker", "exchange", "symbol"),
    [
        ("BRK-B", "NYSE", "BRK.B"),
        ("BRK/B", "NYSE", "BRK.B"),
        ("BFB", "NYSE", "BF.B"),
        ("BFB", "NASDAQ", "BFB"),
        ("SENEB", "NASDAQ", "SENEB"),
        ("KO", "NYSE", "KO"),
    ],
)
def test_symbol_for_maps_a_class_share_ticker_to_alpacas_form(
    ticker: str, exchange: str, symbol: str
) -> None:
    assert symbol_for(ticker, exchange=exchange, class_symbols=CLASS_SYMBOLS) == symbol


@pytest.mark.parametrize(
    ("symbol", "ticker"),
    [("BRK.B", "BRK-B"), ("BF.B", "BFB"), ("ko", "KO"), ("SENEB", "SENEB")],
)
def test_ticker_for_maps_the_symbol_back_through_the_same_rule(symbol: str, ticker: str) -> None:
    assert ticker_for(symbol, LISTINGS, class_symbols=CLASS_SYMBOLS) == ticker
    row = next(r for r in LISTINGS if r["ticker"] == ticker)
    assert (
        symbol_for(ticker, exchange=row["exchange"], class_symbols=CLASS_SYMBOLS) == symbol.upper()
    )


def test_an_unmapped_or_ambiguous_symbol_raises_system_fault() -> None:
    with pytest.raises(SystemFaultError, match="no master ticker"):
        ticker_for("BRK.A", LISTINGS, class_symbols=CLASS_SYMBOLS)
    both = [*LISTINGS, {"ticker": "BRK/B", "exchange": "NYSE"}]
    with pytest.raises(SystemFaultError, match="ambiguous"):
        ticker_for("BRK.B", both, class_symbols=CLASS_SYMBOLS)
    with pytest.raises(SystemFaultError, match="no Alpaca symbol"):
        symbol_for("PG25", exchange="NYSE", class_symbols=CLASS_SYMBOLS)

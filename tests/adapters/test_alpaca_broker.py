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
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from requests.adapters import HTTPAdapter

from tradepartner.adapters import alpaca_broker as broker_mod
from tradepartner.adapters import alpaca_trading_raw as raw_mod
from tradepartner.adapters.alpaca_broker import (
    STATUS_TABLE,
    AlpacaBroker,
    AlpacaBrokerConfigError,
    symbol_for,
    ticker_for,
)
from tradepartner.adapters.alpaca_trading_raw import (
    AlpacaPaperCredentialsError,
    AlpacaPaperGuardError,
    AlpacaTradingError,
    AlpacaTradingRaw,
)
from tradepartner.adapters.broker import (
    UNRESOLVED_ASSET,
    Account,
    Asset,
    CashActivity,
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
from tradepartner.errors import (
    AssetNotFoundError,
    AssetNotTradableError,
    ClockError,
    NotFractionableError,
    ReconciliationError,
    SystemFaultError,
)

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
    return AlpacaBroker(settings, clock, client=raw, book_id="main"), http


def _activity_broker() -> tuple[AlpacaBroker, Recorded]:
    """A broker whose `GET /v2/account/activities` answers the activities page
    it is handed (one page: less than a page stops the read)."""
    broker, http = _broker()

    def activities(request: requests.PreparedRequest) -> tuple[int, Any]:
        return 200, _load("cash_activities")

    http.route("GET", "/v2/account/activities", activities)
    return broker, http


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
    """A request carrying its intent explicitly (T226): the long intent of its
    side unless `position_intent` is overridden."""
    values: dict[str, Any] = {
        "client_order_id": "rec20261008151015-1",
        "symbol": "KO",
        "side": Side.BUY,
        "notional": 5.0,
    }
    values.update(overrides)
    values.setdefault(
        "position_intent", "buy_to_open" if values["side"] is Side.BUY else "sell_to_close"
    )
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
    broker = AlpacaBroker(settings, lambda: NOW, client=raw, book_id="main")

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
    broker = AlpacaBroker(settings, lambda: NOW, client=raw, book_id="main")

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
    http.route("GET", "/v2/account/activities", (200, _load("cash_activities")))


def test_the_base_url_of_every_request_is_the_paper_endpoint() -> None:
    broker, http = _broker()
    _route_everything(http)

    broker.submit(_request())
    broker.cancel("rec20261008151015-5")
    broker.get_order("rec20261008151015-5")
    broker.open_orders()
    broker.fills()
    broker.cash_activities(date(2026, 10, 9))
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
        AlpacaBroker(settings, lambda: NOW, book_id="main")
    text = str(err.value)
    assert "ALPACA_PAPER_API_KEY" in text
    for secret in (PAPER_SECRET, DATA_KEY, DATA_SECRET):
        assert secret not in text


def test_the_adapter_builds_its_raw_client_on_the_books_own_pair() -> None:
    """ADR 0017 B.2 (T153): `AlpacaBroker` for book `b` reads `b`'s pair, never `main`'s."""
    b_key, b_secret = "PKBOOKBFAKE0987654321", "bookBSecretFake0987654321zyxw"  # gitleaks:allow
    settings = _settings({"alpaca_paper_books": {"b": {"api_key": b_key, "api_secret": b_secret}}})
    broker = AlpacaBroker(settings, lambda: NOW, book_id="b")
    client: Any = broker._raw._client
    assert broker.book_id == "b" and broker._raw.book_id == "b"
    assert client._api_key == b_key and client._secret_key == b_secret
    assert client._base_url == "https://paper-api.alpaca.markets"


def test_a_book_with_no_pair_is_refused_before_any_client_is_built() -> None:
    with pytest.raises(AlpacaPaperCredentialsError) as err:
        AlpacaBroker(_settings(), lambda: NOW, book_id="b")
    text = str(err.value)
    assert "ALPACA_PAPER_BOOKS__B__API_KEY" in text
    for secret in (PAPER_KEY, PAPER_SECRET, DATA_KEY, DATA_SECRET):
        assert secret not in text


def test_an_injected_raw_client_of_another_book_is_refused() -> None:
    raw = AlpacaTradingRaw(_settings(), client=CountingClient(), clock=FakeClock())  # type: ignore[arg-type]
    with pytest.raises(AlpacaPaperGuardError, match="'main'"):
        AlpacaBroker(_settings(), lambda: NOW, client=raw, book_id="b")


@pytest.mark.parametrize("unset", ["quantity_decimals", "client_order_id_max_length"])
def test_construction_is_refused_while_a_broker_fact_is_unset(unset: str) -> None:
    settings = _settings(**{unset: None})
    raw = AlpacaTradingRaw(_settings(), client=CountingClient(), clock=FakeClock())  # type: ignore[arg-type]
    with pytest.raises(AlpacaBrokerConfigError, match=f"alpaca.{unset}"):
        AlpacaBroker(settings, lambda: NOW, client=raw, book_id="main")
    with pytest.raises(AlpacaBrokerConfigError):
        AlpacaBroker(settings, lambda: NOW, book_id="main")


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
    "cash_activities": lambda b: b.cash_activities(date(2026, 10, 9)),
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
        # T226 (paper spec amendment #1872 item 3): sent on every order, so the
        # broker never infers a short from an oversell.
        "position_intent": "buy_to_open",
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
    # T226: the long sell names its intent, the one the broker recorded.
    assert body["position_intent"] == "sell_to_close" == sell["submit"]["position_intent"]
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


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # Duplicate fixture plus three 2026-10-19 sandbox JSONL response bodies.
        ("client_order_id must be unique", DuplicateClientOrderIdError),
        ("invalid position_intent specified", AlpacaTradingError),
        ("qty must be > 0", AlpacaTradingError),
        (
            "trailing stop orders cannot specify both trail_price and trail_percent",
            AlpacaTradingError,
        ),
    ],
)
def test_40010001_refusals_are_classified_by_message(
    message: str, expected: type[Exception]
) -> None:
    recorded = _load("duplicate_client_order_id")["error"]
    body = json.loads(recorded["body"])
    body["message"] = message
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id({}))
    http.route("POST", "/v2/orders", (recorded["status_code"], body))

    with pytest.raises(expected) as err:
        broker.submit(_request())
    assert type(err.value) is expected
    if isinstance(err.value, AlpacaTradingError):
        assert err.value.status_code == 422
        assert json.loads(err.value.body) == body


@pytest.mark.parametrize(
    ("name", "symbol", "expected"),
    [
        # "insufficient qty available": the ledger and the broker disagree (spec
        # amendment 2026-10-18 item 2), mapped here, whatever the order.
        ("sell_above_held", "KO", ReconciliationError),
        # Mapped by the refusal, not the side: the wrapper makes it a
        # `ReconciliationError` on a sell (the #395 deliberate fractional exit).
        ("non_fractionable_sell_fractional", "OCGN", NotFractionableError),
    ],
)
def test_a_recorded_submit_refusal_is_raised_typed_with_no_key_in_it(
    name: str, symbol: str, expected: type[Exception]
) -> None:
    error = _load(name)["error"]
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id({}))
    http.route("POST", "/v2/orders", (error["status_code"], json.loads(error["body"])))

    with pytest.raises(expected) as err:
        # The recorded refusals name their own symbol (KO, OCGN): a 403 maps only
        # for the order's own symbol (T202 safety review).
        broker.submit(_request(symbol=symbol, side=Side.SELL, notional=None, quantity=0.5))
    assert not isinstance(err.value, DuplicateClientOrderIdError)
    text = str(err.value) + repr(vars(err.value))
    for secret in (PAPER_KEY, PAPER_SECRET, DATA_KEY, DATA_SECRET):
        assert secret not in text


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
def test_a_wash_trade_403_is_a_reconciliation_fault_by_code_and_message(side: Side) -> None:
    """#1949: 403 40310000 'potential wash trade detected' is typed on either side,
    and the error names it; the same code with another message is not."""
    body = {"code": 40310000, "message": "potential wash trade detected. use complex orders"}
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id({}))
    http.route("POST", "/v2/orders", (403, body))
    with pytest.raises(ReconciliationError, match="wash trade"):
        broker.submit(_request(side=side))


@pytest.mark.parametrize(
    "message",
    [
        "insufficient buying power",
        "opg orders must be submitted after 7:00pm and before 9:28am",
        "bracket orders do not support extended hours trading",
    ],
)
def test_other_40310000_messages_are_not_typed_as_a_wash_trade(message: str) -> None:
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id({}))
    http.route("POST", "/v2/orders", (403, {"code": 40310000, "message": message}))
    with pytest.raises(AlpacaTradingError) as err:
        broker.submit(_request())
    assert not isinstance(err.value, ReconciliationError)


#: The three per-name refusals of the allowlist (paper spec amendment 2026-10-18
#: item 2, T202), each as Alpaca answers `POST /v2/orders`: the 404 / 40410000 of an
#: asset it does not know, and the 403 / 40310000 refusals told apart by message
#: (the not-fractionable body is recorded; the not-tradable one is the fake's, since
#: Alpaca's exact submit-time body is unrecorded, so the message is what is matched).
PER_NAME_REFUSALS = [
    (404, {"code": 40410000, "message": "asset not found for ZZZZ"}, AssetNotFoundError),
    (403, {"code": 40310000, "message": 'asset "KO" is not fractionable'}, NotFractionableError),
    (403, {"code": 40310000, "message": 'asset "KO" is not tradable'}, AssetNotTradableError),
]


@pytest.mark.parametrize(("status", "body", "expected"), PER_NAME_REFUSALS)
def test_a_per_name_refusal_on_a_buy_is_raised_as_its_typed_error(
    status: int, body: dict[str, Any], expected: type[Exception]
) -> None:
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id({}))
    http.route("POST", "/v2/orders", (status, body))

    with pytest.raises(expected) as err:
        broker.submit(_request())
    assert not isinstance(err.value, SystemFaultError)
    assert len(http.calls("POST", "/v2/orders")) == 1
    text = str(err.value) + repr(vars(err.value))
    for secret in (PAPER_KEY, PAPER_SECRET, DATA_KEY, DATA_SECRET):
        assert secret not in text


#: T202's review (safety, SHOULD FIX 1): the map is exact. A 403 is a skip only with
#: code 40310000 and the message `asset "<the order's symbol>" is not
#: tradable|fractionable`; anything near it is raised as is, so the run halts.
NEAR_MISSES = [
    # another symbol's refusal
    (403, {"code": 40310000, "message": 'asset "ZZZ" is not tradable'}),
    (403, {"code": 40310000, "message": 'asset "OCGN" is not fractionable'}),
    # the right text under another code
    (403, {"code": 40310001, "message": 'asset "KO" is not tradable'}),
    (403, {"code": 40310001, "message": 'asset "KO" is not fractionable'}),
    (403, {"message": 'asset "KO" is not tradable'}),
    # an account-wide refusal that merely contains the words
    (403, {"code": 40310000, "message": "account is not tradable"}),
    (403, {"code": 40310000, "message": 'account blocked: asset "KO" is not tradable'}),
]


@pytest.mark.parametrize(("status", "body"), NEAR_MISSES)
def test_a_refusal_near_the_allowlist_is_raised_as_is(status: int, body: dict[str, Any]) -> None:
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id({}))
    http.route("POST", "/v2/orders", (status, body))

    with pytest.raises(AlpacaTradingError) as err:
        broker.submit(_request(symbol="KO"))
    assert err.value.status_code == status
    assert not isinstance(
        err.value, (AssetNotFoundError, NotFractionableError, AssetNotTradableError)
    )


@pytest.mark.parametrize(("status", "body", "typed"), PER_NAME_REFUSALS)
def test_a_per_name_refusal_after_a_retry_is_raised_as_is(
    status: int, body: dict[str, Any], typed: type[Exception]
) -> None:
    """A refusal answered to a retried submit (`error.retried`: an earlier attempt
    timed out or met a 5xx, and the broker may hold that attempt's order) is
    never a skip: it is raised unchanged, so the run halts and resume settles it."""
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id({}))
    answers = iter([(503, {"code": 50300000, "message": "service unavailable"}), (status, body)])
    http.route("POST", "/v2/orders", lambda _request: next(answers))

    with pytest.raises(AlpacaTradingError) as err:
        broker.submit(_request(symbol="KO"))
    assert err.value.retried is True
    assert err.value.status_code == status
    assert not isinstance(err.value, typed)
    assert len(http.calls("POST", "/v2/orders")) == 2


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (422, {"code": 42210000, "message": "qty must be > 0"}),
        (403, {"code": 40310000, "message": "insufficient buying power"}),
        (403, {"code": 40310000, "message": "trading is not allowed while market is closed"}),
        (404, {"code": 40400000, "message": "route not found"}),
    ],
)
def test_any_other_4xx_on_submit_is_raised_as_is(status: int, body: dict[str, Any]) -> None:
    """Outside the allowlist nothing is mapped: the raw error halts under req 4."""
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id({}))
    http.route("POST", "/v2/orders", (status, body))

    with pytest.raises(AlpacaTradingError) as err:
        broker.submit(_request())
    assert err.value.status_code == status
    assert not isinstance(
        err.value, (AssetNotFoundError, NotFractionableError, AssetNotTradableError)
    )


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


#: The three Alpaca paper rows of order 30e87f04 (book daily, 2026-10-20, #2149):
#: a `fill` of 1 corrected 2 ms later by a `partial_fill` of -0.64777629.
_RAL_ORDER = "30e87f04-7701-4498-92cc-5a191a2e6c12"
_RAL_ROWS: list[dict[str, Any]] = [
    {
        "id": "20261020093049795::a",
        "activity_type": "FILL",
        "transaction_time": "2026-10-20T13:30:49.795092Z",
        "type": "partial_fill",
        "price": "67.23",
        "qty": "7",
        "side": "buy",
        "symbol": "RAL",
        "leaves_qty": "0.352223709",
        "order_id": _RAL_ORDER,
        "cum_qty": "7",
        "order_status": "partially_filled",
    },
    {
        "id": "20261020093112899::06fa04cb-5704-494f-b5ac-aadcd8ffa6b0",
        "activity_type": "FILL",
        "transaction_time": "2026-10-20T13:31:12.899373Z",
        "type": "fill",
        "price": "67.23",
        "qty": "1",
        "side": "buy",
        "symbol": "RAL",
        "leaves_qty": "0",
        "order_id": _RAL_ORDER,
        "cum_qty": "7.35222371",
        "order_status": "filled",
    },
    {
        "id": "20261020093112901::df3177eb-37b1-42bb-b404-ea6e94174a75",
        "activity_type": "FILL",
        "transaction_time": "2026-10-20T13:31:12.901007Z",
        "type": "partial_fill",
        "price": "67.23",
        "qty": "-0.64777629",
        "side": "buy",
        "symbol": "RAL",
        "leaves_qty": "1",
        "order_id": _RAL_ORDER,
        "cum_qty": "6.35222371",
        "order_status": "partially_filled",
    },
]


def _ral_broker(rows: list[dict[str, Any]]) -> AlpacaBroker:
    broker, http = _broker()
    http.route("GET", "/v2/account/activities/FILL", (200, rows))
    order = {"id": _RAL_ORDER, "client_order_id": "daily-20261020-ral"}
    http.route("GET", "/v2/orders/[^/]+", _by_id({_RAL_ORDER: order}))
    return broker


def test_a_negative_fill_row_corrects_its_orders_earlier_fill() -> None:
    fills = _ral_broker(copy.deepcopy(_RAL_ROWS)).fills()

    assert [(f.broker_fill_id, f.quantity) for f in fills] == [
        ("20261020093049795::a", 7.0),
        ("20261020093112899::06fa04cb-5704-494f-b5ac-aadcd8ffa6b0", 0.35222371),
    ]
    assert sum(f.quantity for f in fills) == 7.35222371  # the broker's position
    assert fills[1].filled_at == datetime(2026, 10, 20, 13, 31, 12, 899373, tzinfo=UTC)
    assert fills[1].price == 67.23 and fills[1].client_order_id == "daily-20261020-ral"


def test_a_negative_fill_row_with_no_earlier_fill_in_the_read_raises_system_fault() -> None:
    since = datetime(2026, 10, 20, 13, 31, 12, 900000, tzinfo=UTC)  # between the two rows
    with pytest.raises(SystemFaultError, match="has no earlier fill"):
        _ral_broker(copy.deepcopy(_RAL_ROWS)).fills(since)
    with pytest.raises(SystemFaultError, match="has no earlier fill"):
        _ral_broker(copy.deepcopy(_RAL_ROWS[2:])).fills()


def test_a_negative_fill_row_at_another_price_or_past_zero_raises_system_fault() -> None:
    repriced = copy.deepcopy(_RAL_ROWS)
    repriced[2]["price"] = "67.24"
    with pytest.raises(SystemFaultError, match=r"corrects a fill at 67\.23"):
        _ral_broker(repriced).fills()

    too_big = copy.deepcopy(_RAL_ROWS)
    too_big[2]["qty"] = "-1"
    with pytest.raises(SystemFaultError, match="positive, finite"):
        _ral_broker(too_big).fills()


@pytest.mark.parametrize("field, value", [("symbol", "OTHER"), ("side", "sell")])
def test_a_negative_fill_row_with_another_symbol_or_side_raises_system_fault(
    field: str, value: str
) -> None:
    changed = copy.deepcopy(_RAL_ROWS)
    changed[2][field] = value

    with pytest.raises(SystemFaultError, match=field):
        _ral_broker(changed).fills()


def _short_rows(side: str, correction_side: str | None = None) -> list[dict[str, Any]]:
    """The RAL rows as a short sale (`side`), the correction on `correction_side`."""
    rows = copy.deepcopy(_RAL_ROWS)
    for row in rows:
        row["side"] = side
    rows[2]["side"] = correction_side or side
    return rows


def _short_fills_broker() -> AlpacaBroker:
    """The recorded sandbox AAPL short open (`sell_short`) and its cover (`buy`),
    ids scrubbed (`short_fills.json`, from the 2026-10-19 probe, batch 1)."""
    rows = _load("short_fills")
    broker, http = _broker()
    http.route("GET", "/v2/account/activities/FILL", (200, rows))
    orders = {
        row["order_id"]: {"id": row["order_id"], "client_order_id": f"short-{n}"}
        for n, row in enumerate(rows)
    }
    http.route("GET", "/v2/orders/[^/]+", _by_id(orders))
    return broker


def test_a_sell_short_fill_parses_as_a_sell() -> None:
    fill = _short_fills_broker().fills()[0]

    assert (fill.side, fill.symbol, fill.quantity, fill.price) == (Side.SELL, "AAPL", 1.0, 342.02)


def test_a_cover_fill_parses_as_a_buy() -> None:
    fill = _short_fills_broker().fills()[1]

    assert (fill.side, fill.symbol, fill.quantity, fill.price) == (Side.BUY, "AAPL", 1.0, 342.06)


def test_a_sell_short_correction_folds_into_its_sell_short_fill() -> None:
    fills = _ral_broker(_short_rows("sell_short")).fills()

    assert [(f.side, f.quantity) for f in fills] == [(Side.SELL, 7.0), (Side.SELL, 0.35222371)]


def test_a_correction_on_the_opposite_side_of_a_sell_short_fill_raises_system_fault() -> None:
    with pytest.raises(SystemFaultError, match="side"):
        _ral_broker(_short_rows("sell_short", "buy")).fills()


@pytest.mark.parametrize("side", ["buy_to_cover", "short", ""])
def test_an_unclassified_fill_side_raises_system_fault(side: str) -> None:
    with pytest.raises(SystemFaultError, match="not classified"):
        _ral_broker(_short_rows(side)[:1]).fills()


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


def test_an_asset_the_broker_answers_404_is_unresolved_not_omitted() -> None:
    """#1729 (was #410's halt): a symbol Alpaca does not know (404, code
    40410000, on `GET /v2/assets/{symbol}`) answers `UNRESOLVED_ASSET`, never
    tradable, so the run skips that one name instead of halting the book;
    `Broker.assets` keeps its one-`Asset`-per-symbol contract."""
    broker, http = _broker()
    ko = _load("assets")[0]
    http.route("GET", "/v2/assets/KO", (200, ko))
    http.route("GET", "/v2/assets/GONE", (404, {"code": 40410000, "message": "asset not found"}))
    answer = broker.assets(["KO", "GONE"])
    assert answer["GONE"] == UNRESOLVED_ASSET
    assert not answer["GONE"].tradable
    assert answer["KO"].tradable


def test_an_asset_read_404_with_another_code_raises() -> None:
    """Only Alpaca's asset-not-found code is an unresolved name; any other 404
    is a fault and halts fail-closed."""
    broker, http = _broker()
    http.route("GET", "/v2/assets/GONE", (404, {"code": 40400000, "message": "not found"}))
    with pytest.raises(AlpacaTradingError) as raised:
        broker.assets(["GONE"])
    assert raised.value.status_code == 404


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


# --- review fixes (pass 1) ---------------------------------------------------------


@pytest.mark.parametrize("status", [422, 404])
def test_a_cancel_refused_because_the_order_finished_raises_order_not_open(status: int) -> None:
    resting = _load("resting")["polls"][-1]
    finished = dict(
        _load("resting_cancelled")[0],
        status="filled",
        filled_qty="1",
        filled_avg_price="78.19",
        filled_at="2026-10-08T15:10:22.1Z",
    )
    state = {"rec20261008151015-5": resting}
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id(state))

    def delete(_request: requests.PreparedRequest) -> tuple[int, Any]:
        state["rec20261008151015-5"] = finished
        return status, {"code": 42210000, "message": "order is not cancelable"}

    http.route("DELETE", "/v2/orders/[^/]+", delete)
    with pytest.raises(OrderNotOpenError):
        broker.cancel("rec20261008151015-5")


def test_a_cancel_refused_while_the_order_is_still_open_is_raised() -> None:
    resting = _load("resting")["polls"][-1]
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", (200, resting))
    http.route("DELETE", "/v2/orders/[^/]+", (422, {"code": 42210000, "message": "no"}))
    with pytest.raises(AlpacaTradingError) as err:
        broker.cancel("rec20261008151015-5")
    assert err.value.status_code == 422


def test_fills_before_since_resolve_no_order() -> None:
    broker, http = _broker()
    http.route("GET", "/v2/account/activities/FILL", (200, _load("fill_activities")))
    finals = _final_orders()
    first_order = _load("fill_activities")[0]["order_id"]
    del finals[first_order]  # the first fill's order cannot be resolved
    http.route("GET", "/v2/orders/[^/]+", _by_id(finals))

    fills = broker.fills(datetime(2026, 10, 8, 15, 10, 19, tzinfo=UTC))

    assert len(fills) == 5
    resolved = [urlsplit(r.url or "").path for r in http.calls("GET", "/v2/orders/[^/]+")]
    assert not any(path.endswith(first_order) for path in resolved)


# --- non-trade cash activities (paper-trading spec req 1 item 1, #1583, T183b) ----


def test_the_recorded_fees_map_to_cash_activities_with_no_time() -> None:
    """The scrubbed fixture's two `FEE` rows (dated 2026-10-09, -0.01 each,
    `status = executed`, no `transaction_time`): the date stays a **date** —
    no time is invented for a row that has none — and the ids are as given."""
    broker, _ = _activity_broker()

    rows = broker.cash_activities(date(2026, 10, 9))

    assert rows == [
        CashActivity(
            activity_id="fixture-fee-cat-2026-10-09",
            activity_type="FEE",
            date=date(2026, 10, 9),
            net_amount=-0.01,
            status="executed",
            symbol=None,
            description="CAT fee for proceed of 8 trades on 2026-10-09",
        ),
        CashActivity(
            activity_id="fixture-fee-reg-2026-10-09",
            activity_type="FEE",
            date=date(2026, 10, 9),
            net_amount=-0.01,
            status="executed",
            symbol=None,
            description="REG fee for proceed of $433.15 on 2026-10-09",
        ),
    ]
    for row in rows:
        assert type(row.date) is date
    assert not any(hasattr(row.date, name) for row in rows for name in ("hour", "tzinfo"))


def test_a_fill_row_in_the_page_is_dropped_client_side() -> None:
    """The request carries no type filter, so a `FILL` row can be in the page;
    a fill is never a cash activity (spec item 1's "it never returns a fill")."""
    broker, http = _broker()
    fill = _load("fill_activities")[0]
    page = [_load("cash_activities")[0], dict(fill, activity_type="FILL")]
    http.route("GET", "/v2/account/activities", (200, page))

    rows = broker.cash_activities(date(2026, 10, 9))

    assert [r.activity_type for r in rows] == ["FEE"]


def test_an_unclassified_type_is_returned_not_refused() -> None:
    """`INT` and `SSP` are types the adapter has never seen: they reach the
    reconciliation instead of being filtered away, so an unknown cash
    movement halts there rather than passing silently."""
    broker, http = _broker()
    http.route(
        "GET",
        "/v2/account/activities",
        (
            200,
            [
                {
                    "id": "act-int",
                    "activity_type": "INT",
                    "date": "2026-10-09",
                    "net_amount": "0.01",
                    "status": "executed",
                },
                {
                    "id": "act-ssp",
                    "activity_type": "SSP",
                    "date": "2026-10-09",
                    "net_amount": None,
                    "status": "executed",
                },
            ],
        ),
    )

    rows = broker.cash_activities(date(2026, 10, 9))

    assert [(r.activity_id, r.activity_type, r.net_amount) for r in rows] == [
        ("act-int", "INT", 0.01),
        ("act-ssp", "SSP", None),
    ]


def test_a_row_dated_before_since_is_returned_too() -> None:
    """Nothing is dropped by date: the adapter asks from earlier than `since`
    and returns every row it gets, because the caller's bound is the one that
    counts (spec item 2)."""
    broker, http = _broker()
    page = [
        {
            "id": "act-earlier",
            "activity_type": "FEE",
            "date": "2026-10-08",
            "net_amount": "-0.02",
            "status": "executed",
            "description": "REG fee for proceed of $527.47",
        },
        *_load("cash_activities"),
    ]
    http.route("GET", "/v2/account/activities", (200, page))

    rows = broker.cash_activities(date(2026, 10, 9))

    assert [(r.activity_id, r.date) for r in rows] == [
        ("act-earlier", date(2026, 10, 8)),
        ("fixture-fee-cat-2026-10-09", date(2026, 10, 9)),
        ("fixture-fee-reg-2026-10-09", date(2026, 10, 9)),
    ]


def test_the_activities_request_asks_from_the_new_york_midnight_before_since() -> None:
    """`after` is the New York midnight that begins the day before the New York
    date `since`, and the request carries no `activity_types` filter (spec
    item 2): Alpaca's `after` is strict and its ordering of a date-only row is
    unknown, so a read bounded at `since`'s own midnight could miss a row
    dated `since`."""
    broker, http = _activity_broker()

    broker.cash_activities(date(2026, 10, 9))

    (read,) = http.calls("GET", "/v2/account/activities")
    query = parse_qs(urlsplit(read.url or "").query)
    assert query["after"] == ["2026-10-08T04:00:00+00:00"]  # 2026-10-09 less 1 day, EDT
    assert "activity_types" not in query
    assert query["direction"] == ["asc"]


@pytest.mark.parametrize(
    ("since", "after"),
    [
        (date(2026, 7, 2), "2026-07-01T04:00:00+00:00"),  # EDT
        (date(2026, 3, 10), "2026-03-09T04:00:00+00:00"),  # EDT, the day after the switch
        (date(2026, 3, 9), "2026-03-08T05:00:00+00:00"),  # the day before the 2026-03-08 switch
        (date(2026, 11, 3), "2026-11-02T05:00:00+00:00"),  # EST, after the 2026-11-01 switch
    ],
)
def test_the_reads_start_is_one_new_york_day_before_since_across_dst(
    since: date, after: str
) -> None:
    """The midnight of `since` less one day is a DST-correct UTC instant, so
    the read never starts inside the wrong day."""
    broker, http = _activity_broker()

    broker.cash_activities(since)

    (read,) = http.calls("GET", "/v2/account/activities")
    assert parse_qs(urlsplit(read.url or "").query)["after"] == [after]


def test_the_activities_read_pages_like_the_fills_read() -> None:
    """Paging stops as the fills read does: a short page ends the read, a full
    page is followed by its `page_token`, and a page that does not advance is
    refused rather than looped."""
    size = raw_mod.ACTIVITIES_PAGE_SIZE
    broker, http = _activity_broker()
    http.routes.clear()

    def fee(index: object) -> dict[str, Any]:
        return {
            "id": f"a{index}",
            "activity_type": "FEE",
            "date": "2026-10-09",
            "net_amount": "-0.01",
            "status": "executed",
        }

    def paged(request: requests.PreparedRequest) -> tuple[int, Any]:
        token = parse_qs(urlsplit(request.url or "").query).get("page_token", [None])[0]
        if token is None:
            return 200, [fee(i) for i in range(size)]
        return 200, [fee("last")]

    http.route("GET", "/v2/account/activities", paged)
    rows = broker.cash_activities(date(2026, 10, 9))
    assert len(rows) == size + 1
    assert len(http.calls("GET", "/v2/account/activities")) == 2

    http.route("GET", "/v2/account/activities", (200, [fee(i) for i in range(size)]))
    with pytest.raises(RuntimeError, match="account activities paging did not advance"):
        broker.cash_activities(date(2026, 10, 9))


def test_a_page_element_that_is_not_an_object_raises_system_fault() -> None:
    """A page holding something that is not an activity object is malformed:
    the adapter fails closed with `SystemFaultError` rather than raising an
    `AttributeError` from inside the loop (safety-reviewer's fix)."""
    broker, http = _activity_broker()
    for page in (["not-an-object"], [{"id": "a", "activity_type": "FEE"}, 7]):
        http.route("GET", "/v2/account/activities", (200, page))
        with pytest.raises(SystemFaultError, match="malformed alpaca activity"):
            broker.cash_activities(date(2026, 10, 9))


def test_a_row_the_adapter_cannot_map_raises_system_fault() -> None:
    """A payload with no `id`, no `date`, no `status`, or a `date` carrying a
    time the row does not have, is a malformed activity: fail closed rather
    than guess."""
    broker, http = _activity_broker()
    for bad in (
        {"activity_type": "FEE", "date": "2026-10-09", "net_amount": "-0.01", "status": "executed"},
        {"id": "act", "activity_type": "FEE", "net_amount": "-0.01", "status": "executed"},
        {"id": "act", "activity_type": "FEE", "date": "2026-10-09", "net_amount": "-0.01"},
        {
            "id": "act",
            "activity_type": "FEE",
            "date": "2026-10-09T00:00:00Z",
            "net_amount": "-0.01",
            "status": "executed",
        },
    ):
        http.route("GET", "/v2/account/activities", (200, [bad]))
        with pytest.raises(SystemFaultError, match="malformed alpaca activity"):
            broker.cash_activities(date(2026, 10, 9))


def test_a_read_activity_row_names_no_symbol_and_keeps_one_that_does() -> None:
    """A `FEE` row names no symbol; one that does is canonicalized like every
    symbol in this module."""
    broker, http = _activity_broker()
    http.route(
        "GET",
        "/v2/account/activities",
        (
            200,
            [
                {
                    "id": "act-div",
                    "activity_type": "DIV",
                    "date": "2026-10-09",
                    "net_amount": "1.25",
                    "status": "executed",
                    "symbol": "ko",
                    "description": "Cash dividend of 1.25 on KO",
                },
            ],
        ),
    )

    (row,) = broker.cash_activities(date(2026, 10, 9))

    assert row.symbol == "KO" and row.description == "Cash dividend of 1.25 on KO"


def test_cash_activities_reads_the_clock_before_any_request() -> None:
    """ADR 0007 point 4: the wrapped clock reading comes before every request,
    this new read included."""
    broker, http = _broker(_raises)
    with pytest.raises(ClockError):
        broker.cash_activities(date(2026, 10, 9))
    assert http.sent == []


def test_a_whole_share_float_quantity_passes_a_zero_decimal_precision() -> None:
    broker, http = _broker(settings=_settings(quantity_decimals=0))
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id({}))
    http.route("POST", "/v2/orders", (200, _load("buy_whole")["submit"]))
    request = _request(
        client_order_id=_load("buy_whole")["submit"]["client_order_id"],
        notional=None,
        quantity=1.0,
    )
    assert broker.submit(request).quantity == 1.0


def test_an_asset_read_answering_fewer_symbols_raises_system_fault() -> None:
    settings = _settings()
    raw = AlpacaTradingRaw(settings, clock=FakeClock())
    raw.get_assets = lambda symbols: []  # type: ignore[method-assign]
    with pytest.raises(SystemFaultError, match="0 of 1"):
        AlpacaBroker(settings, lambda: NOW, client=raw, book_id="main").assets(["KO"])


# --- T226: the four intents on the wire, and `paper` (paper spec amendment #1872) ------


@pytest.mark.parametrize(
    ("name", "size"),
    [("buy_fractional", {"notional": 5.0}), ("buy_whole", {"quantity": 1.0})],
)
def test_a_recorded_long_buy_is_todays_body_plus_its_intent(
    name: str, size: dict[str, float]
) -> None:
    """The recorded `buy_*.json` pins re-read with the one field: the body is
    field for field today's (symbol, side, type, time in force, id, size) plus
    `position_intent`, and the intent sent is the one the broker inferred when
    T48b recorded it without one."""
    recorded = _load(name)
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id({}))
    http.route("POST", "/v2/orders", (200, recorded["submit"]))
    coid = recorded["submit"]["client_order_id"]

    broker.submit(_request(**{"client_order_id": coid, "notional": None, **size}))

    body = json.loads(http.calls("POST", "/v2/orders")[0].body or b"{}")
    wire = {"notional": size["notional"]} if "notional" in size else {"qty": size["quantity"]}
    assert body == {
        "symbol": "KO",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "client_order_id": coid,
        **wire,
        "position_intent": "buy_to_open",
    }
    assert body["position_intent"] == recorded["submit"]["position_intent"]


#: STUB (plan rule 4, T226): Alpaca's order payload for a 1-share `sell_to_open`
#: of AAPL, as the paper sandbox answered `POST /v2/orders` on 2026-10-19
#: (`intent.short_sell_to_open`, batch1). T227's `short_open.json` replaces it.
SHORT_OPEN_STUB: dict[str, Any] = {
    "id": "b9fdfcab-8c5f-403f-8cd6-d6864ba6a37f",
    "client_order_id": "tp-short-1",
    "created_at": "2026-10-19T17:27:29.19551305Z",
    "updated_at": "2026-10-19T17:27:29.196355824Z",
    "submitted_at": "2026-10-19T17:27:29.19551305Z",
    "filled_at": None,
    "expired_at": None,
    "canceled_at": None,
    "failed_at": None,
    "replaced_at": None,
    "replaced_by": None,
    "replaces": None,
    "asset_id": "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415",
    "symbol": "AAPL",
    "asset_class": "us_equity",
    "notional": None,
    "qty": "1",
    "filled_qty": "0",
    "filled_avg_price": None,
    "order_class": "",
    "order_type": "market",
    "type": "market",
    "side": "sell",
    "position_intent": "sell_to_open",
    "time_in_force": "day",
    "limit_price": None,
    "stop_price": None,
    "status": "pending_new",
    "extended_hours": False,
    "legs": None,
    "trail_percent": None,
    "trail_price": None,
    "hwm": None,
    "subtag": None,
    "source": None,
    "expires_at": "2026-10-19T20:00:00Z",
}
#: STUB: the cover's payload, the same order with Alpaca's buy-side fields
#: (sandbox `intent.cover_buy_no_intent`'s shape). T227's `short_cover.json`
#: replaces it.
SHORT_COVER_STUB: dict[str, Any] = dict(
    SHORT_OPEN_STUB,
    id="30b8fd01-3521-4553-a8c8-a3ba8ea0cccd",
    client_order_id="tp-cover-1",
    side="buy",
    position_intent="buy_to_close",
)


@pytest.mark.parametrize(
    ("stub", "side", "intent"),
    [
        (SHORT_OPEN_STUB, Side.SELL, "sell_to_open"),
        (SHORT_COVER_STUB, Side.BUY, "buy_to_close"),
    ],
)
def test_a_short_request_sends_its_intent_by_whole_share_quantity(
    stub: dict[str, Any], side: Side, intent: str
) -> None:
    broker, http = _broker()
    http.route("GET", "/v2/orders:by_client_order_id", _by_client_id({}))
    http.route("POST", "/v2/orders", (200, stub))

    order = broker.submit(
        _request(
            client_order_id=stub["client_order_id"],
            symbol="AAPL",
            side=side,
            notional=None,
            quantity=1.0,
            position_intent=intent,
        )
    )

    body = json.loads(http.calls("POST", "/v2/orders")[0].body or b"{}")
    assert body == {
        "symbol": "AAPL",
        "side": side.value,
        "type": "market",
        "time_in_force": "day",
        "client_order_id": stub["client_order_id"],
        "qty": 1.0,
        "position_intent": intent,
    }
    assert order.side is side and order.quantity == 1.0
    assert order.status is OrderStatus.ACCEPTED and order.broker_order_id == stub["id"]


def test_the_alpaca_adapter_is_paper_with_its_literal() -> None:
    """Spec amendment item 1: `paper` is `True` for the adapter, which refuses
    to build unless `alpaca.paper` is the literal `True` (T48c)."""
    broker, _ = _broker()
    assert broker.paper is True

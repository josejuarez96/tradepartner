"""Offline tests for `adapters.alpaca_trading_raw` (T48): no network.

A fake client stands in for `alpaca-py`'s `TradingClient` and counts calls;
a fake clock stands in for pacing, so nothing sleeps for real.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from alpaca.common.exceptions import APIError

from tradepartner.adapters import alpaca_trading_raw as raw_mod
from tradepartner.adapters.alpaca_trading_raw import (
    AlpacaPaperCredentialsError,
    AlpacaPaperGuardError,
    AlpacaTradingError,
    AlpacaTradingRaw,
)
from tradepartner.config import Settings

PAPER_KEY = "PKPAPERFAKE1234567890"  # gitleaks:allow
PAPER_SECRET = "paperSecretFake1234567890abcdefghij"  # gitleaks:allow
DATA_KEY = "AKDATAFAKE1234567890"  # gitleaks:allow


def _settings(keys: dict[str, Any] | None = None, **alpaca: Any) -> Settings:
    values: dict[str, Any] = {
        "alpaca_paper_api_key": PAPER_KEY,
        "alpaca_paper_api_secret": PAPER_SECRET,
        "alpaca_api_key": DATA_KEY,
        "alpaca_api_secret": "dataSecretFake1234567890abcdef",  # gitleaks:allow
    }
    values.update(keys or {})
    return Settings(_env_file=None, alpaca=alpaca, **values)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeClient:
    """Counts calls; `fail_with` raises that many times before succeeding."""

    def __init__(self, fail_with: list[BaseException] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fail_with = list(fail_with or [])
        self.pages: list[list[dict[str, Any]]] = []

    def _hit(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if self.fail_with:
            raise self.fail_with.pop(0)

    def submit_order(self, order_data: Any) -> Any:
        self._hit("submit_order", order_data)
        return {"id": "o1", "status": "accepted"}

    def cancel_order_by_id(self, order_id: Any) -> Any:
        self._hit("cancel_order_by_id", order_id)
        return None

    def get_order_by_client_id(self, client_id: str) -> Any:
        self._hit("get_order_by_client_id", client_id)
        return {"client_order_id": client_id}

    def get_order_by_id(self, order_id: str) -> Any:
        self._hit("get_order_by_id", order_id)
        return {"id": order_id}

    def get_orders(self, filter: Any = None) -> Any:
        self._hit("get_orders", filter)
        return [{"id": "o1"}]

    def get_all_positions(self) -> Any:
        self._hit("get_all_positions")
        return []

    def get_account(self) -> Any:
        self._hit("get_account")
        return {"cash": "100"}

    def get_asset(self, symbol_or_asset_id: Any) -> Any:
        self._hit("get_asset", symbol_or_asset_id)
        return {"symbol": symbol_or_asset_id}

    def get(self, path: str, data: Any = None) -> Any:
        self._hit("get", path, data)
        return self.pages.pop(0) if self.pages else []


def _raw(settings: Settings | None = None, client: FakeClient | None = None) -> AlpacaTradingRaw:
    return AlpacaTradingRaw(
        settings or _settings(), client=client or FakeClient(), clock=FakeClock()
    )


# --- paper only -------------------------------------------------------------


def test_real_client_is_built_with_literal_paper_true_and_paper_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[dict[str, Any]] = []

    class RecordingTradingClient:
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)
            self._retry = 3
            self._session = requests.Session()

    monkeypatch.setattr(raw_mod, "TradingClient", RecordingTradingClient)
    AlpacaTradingRaw(_settings(), clock=FakeClock())

    assert built == [
        {"api_key": PAPER_KEY, "secret_key": PAPER_SECRET, "paper": True, "raw_data": True}
    ]


def test_every_trading_client_construction_in_the_module_passes_literal_paper_true() -> None:
    tree = ast.parse(inspect.getsource(raw_mod))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "TradingClient"
    ]
    assert calls, "no TradingClient construction found"
    for call in calls:
        keywords = {kw.arg: kw.value for kw in call.keywords}
        assert not call.args and "url_override" not in keywords and None not in keywords
        paper = keywords["paper"]
        assert isinstance(paper, ast.Constant) and paper.value is True


def test_real_client_gets_the_configured_timeout_and_no_sdk_retry() -> None:
    raw = AlpacaTradingRaw(_settings(trading_request_timeout_seconds=7.5), clock=FakeClock())
    client = raw._client
    assert isinstance(client, raw_mod.TradingClient)
    assert client._base_url == "https://paper-api.alpaca.markets"
    assert client._retry == 0
    adapter = client._session.get_adapter("https://paper-api.alpaca.markets/v2/account")
    assert isinstance(adapter, raw_mod._TimeoutAdapter) and adapter.timeout == 7.5


@pytest.mark.parametrize(
    "bypass",
    [
        lambda s: s.model_copy(update={"alpaca": s.alpaca.model_copy(update={"paper": False})}),
        lambda s: s.model_copy(update={"alpaca": type(s.alpaca).model_construct(paper=False)}),
    ],
)
def test_refuses_to_start_when_the_paper_guard_was_bypassed(bypass: Any) -> None:
    client = FakeClient()
    with pytest.raises(AlpacaPaperGuardError):
        AlpacaTradingRaw(bypass(_settings()), client=client, clock=FakeClock())
    assert client.calls == []


@pytest.mark.parametrize(
    ("overrides", "missing"),
    [
        ({"alpaca_paper_api_key": None}, "ALPACA_PAPER_API_KEY"),
        ({"alpaca_paper_api_secret": "   "}, "ALPACA_PAPER_API_SECRET"),
    ],
)
def test_missing_paper_key_raises_before_any_call_with_no_secret_in_text(
    overrides: dict[str, Any], missing: str
) -> None:
    client = FakeClient()
    with pytest.raises(AlpacaPaperCredentialsError) as err:
        AlpacaTradingRaw(_settings(overrides), client=client, clock=FakeClock())
    text = str(err.value)
    assert missing in text
    for secret in (PAPER_KEY, PAPER_SECRET, DATA_KEY):
        assert secret not in text
    assert client.calls == []


def test_data_keys_are_never_a_fallback_for_paper_keys() -> None:
    settings = _settings({"alpaca_paper_api_key": None, "alpaca_paper_api_secret": None})
    with pytest.raises(AlpacaPaperCredentialsError, match="ALPACA_PAPER_API_KEY"):
        AlpacaTradingRaw(settings, client=FakeClient(), clock=FakeClock())


# --- pacing, timeout, retries -----------------------------------------------


def test_requests_are_paced_at_the_configured_rate() -> None:
    clock = FakeClock()
    client = FakeClient()
    raw = AlpacaTradingRaw(_settings(trading_requests_per_minute=30), client=client, clock=clock)

    raw.get_account()
    raw.list_positions()
    clock.now += 0.5
    raw.get_account()

    assert len(client.calls) == 3
    assert clock.sleeps == [pytest.approx(2.0), pytest.approx(1.5)]


def test_a_timeout_is_retried_max_retries_times_then_raised() -> None:
    clock = FakeClock()
    client = FakeClient(fail_with=[requests.Timeout("slow")] * 10)
    raw = AlpacaTradingRaw(_settings(trading_max_retries=2), client=client, clock=clock)

    with pytest.raises(AlpacaTradingError) as err:
        raw.get_account()
    assert len(client.calls) == 3
    assert err.value.status_code is None and err.value.retried
    # No SDK exception (whose request headers carry the keys) kept on the error.
    assert err.value.__context__ is None and err.value.__cause__ is None
    assert PAPER_KEY not in repr(vars(err.value)) and PAPER_KEY not in str(err.value)


def _api_error(status: int) -> APIError:
    http_error = SimpleNamespace(response=SimpleNamespace(status_code=status))
    return APIError('{"code": 1, "message": "refused"}', http_error)


def test_a_429_is_retried_and_a_403_is_not() -> None:
    client = FakeClient(fail_with=[_api_error(429)])
    assert _raw(client=client).get_account() == {"cash": "100"}

    client = FakeClient(fail_with=[_api_error(403)])
    with pytest.raises(AlpacaTradingError) as err:
        _raw(client=client).get_account()
    assert err.value.status_code == 403 and not err.value.retried
    assert len(client.calls) == 1


def test_a_timeout_then_success_returns_the_payload() -> None:
    client = FakeClient(fail_with=[requests.ReadTimeout("slow")])
    raw = _raw(client=client)
    assert raw.get_account() == {"cash": "100"}
    assert len(client.calls) == 2


def test_other_errors_are_not_retried() -> None:
    client = FakeClient(fail_with=[ValueError("bad"), ValueError("bad")])
    with pytest.raises(ValueError):
        _raw(client=client).get_account()
    assert len(client.calls) == 1


# --- thin calls --------------------------------------------------------------


def test_submit_requires_a_client_order_id() -> None:
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    client = FakeClient()
    raw = _raw(client=client)
    request = MarketOrderRequest(
        symbol="KO", notional=5, side=OrderSide.BUY, time_in_force=TimeInForce.DAY
    )
    with pytest.raises(ValueError, match="client_order_id"):
        raw.submit_order(request)
    assert client.calls == []

    request.client_order_id = "rec-1"
    assert raw.submit_order(request) == {"id": "o1", "status": "accepted"}


def _request() -> Any:
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    return MarketOrderRequest(
        symbol="KO",
        notional=5,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        client_order_id="rec-1",
    )


def test_a_submit_refused_as_duplicate_on_retry_returns_the_landed_order() -> None:
    client = FakeClient(fail_with=[requests.ReadTimeout("slow"), _api_error(422)])
    assert _raw(client=client).submit_order(_request()) == {"client_order_id": "rec-1"}
    assert [name for name, _ in client.calls] == [
        "submit_order",
        "submit_order",
        "get_order_by_client_id",
    ]


def test_a_duplicate_on_the_first_submit_attempt_is_raised() -> None:
    client = FakeClient(fail_with=[_api_error(422)])
    with pytest.raises(AlpacaTradingError) as err:
        _raw(client=client).submit_order(_request())
    assert err.value.status_code == 422 and len(client.calls) == 1


def test_an_injected_trading_client_on_the_live_url_is_refused() -> None:
    live = raw_mod.TradingClient(api_key=PAPER_KEY, secret_key=PAPER_SECRET, paper=False)
    with pytest.raises(AlpacaPaperGuardError):
        AlpacaTradingRaw(_settings(), client=live, clock=FakeClock())


def test_thin_calls_reach_the_client() -> None:
    client = FakeClient()
    raw = _raw(client=client)

    assert raw.cancel_order("o1") is None
    assert raw.get_order_by_client_id("rec-1") == {"client_order_id": "rec-1"}
    assert raw.list_open_orders() == [{"id": "o1"}]
    assert raw.list_positions() == []
    assert raw.get_assets(["KO", "SPY"]) == [{"symbol": "KO"}, {"symbol": "SPY"}]

    names = [name for name, _ in client.calls]
    assert names == [
        "cancel_order_by_id",
        "get_order_by_client_id",
        "get_orders",
        "get_all_positions",
        "get_asset",
        "get_asset",
    ]
    open_filter = client.calls[2][1][0]
    assert open_filter.status.value == "open"


def test_open_orders_at_the_page_limit_raise_rather_than_truncate() -> None:
    client = FakeClient()
    client.get_orders = lambda filter=None: [{}] * raw_mod.OPEN_ORDERS_LIMIT  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="truncated"):
        _raw(client=client).list_open_orders()


def test_fill_activities_page_through_after_a_utc_instant() -> None:
    client = FakeClient()
    size = raw_mod.ACTIVITIES_PAGE_SIZE
    client.pages = [[{"id": f"a{i}"} for i in range(size)], [{"id": "last"}]]
    after = datetime(2026, 9, 28, 13, 30, tzinfo=UTC)

    fills = _raw(client=client).list_fill_activities(after)

    assert len(fills) == size + 1
    (_, (path1, params1)), (_, (_, params2)) = client.calls
    assert path1 == "/account/activities/FILL"
    assert params1["after"] == "2026-09-28T13:30:00+00:00" and params1["direction"] == "asc"
    assert "page_token" not in params1 and params2["page_token"] == f"a{size - 1}"


def test_fill_activities_raise_when_paging_does_not_advance() -> None:
    client = FakeClient()
    page = [{"id": f"a{i}"} for i in range(raw_mod.ACTIVITIES_PAGE_SIZE)]
    client.pages = [page, page, page]
    with pytest.raises(RuntimeError, match="did not advance"):
        _raw(client=client).list_fill_activities(datetime(2026, 9, 28, tzinfo=UTC))


def test_fill_activities_refuse_a_naive_instant() -> None:
    with pytest.raises(ValueError):
        _raw().list_fill_activities(datetime(2026, 9, 28, 13, 30))  # noqa: DTZ001


def test_get_order_by_id_is_paced_and_retried_like_every_call() -> None:
    clock = FakeClock()
    client = FakeClient(fail_with=[requests.Timeout("slow")])
    raw = AlpacaTradingRaw(_settings(trading_requests_per_minute=30), client=client, clock=clock)

    assert raw.get_order_by_id("o9") == {"id": "o9"}
    assert client.calls == [("get_order_by_id", ("o9",))] * 2
    assert clock.sleeps == [pytest.approx(2.0)]


@pytest.mark.parametrize(
    ("side", "notional", "qty"), [("buy", 5.0, None), ("sell", None, 0.557436865)]
)
def test_market_day_order_builds_the_one_shape_the_adapter_sends(
    side: str, notional: float | None, qty: float | None
) -> None:
    request = raw_mod.market_day_order("KO", side, "rec-1", notional=notional, qty=qty)
    fields = request.to_request_fields()
    assert fields.pop("side") == side
    assert fields == {
        "symbol": "KO",
        "type": "market",
        "time_in_force": "day",
        "client_order_id": "rec-1",
        **({"notional": notional} if notional is not None else {"qty": qty}),
    }

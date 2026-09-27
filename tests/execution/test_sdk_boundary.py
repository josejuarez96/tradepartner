"""The alpaca-py trading SDK stays inside the adapters (#296).

Static checks over `src/tradepartner/`, tests excepted, so no module can reach
the broker through the SDK and around both the raw client's `paper=True`
guard and the risk-gated wrapper:

1. `alpaca.trading.client` (and `TradingClient`, however reached) is imported
   or read only by `adapters/alpaca_raw.py` (the data adapter, assets only)
   and `adapters/alpaca_trading_raw.py`.
2. `alpaca.trading.requests` (and any `...Request` class re-exported from
   `alpaca.trading`) is imported only by `adapters/alpaca_trading_raw.py` and
   `cli_record.py` (the owner-run paper recorder, T48).
3. A `.submit_order` or `.cancel_order*` attribute, called or passed on as a
   bound method, or named through `getattr`/`methodcaller`, appears only in
   `adapters/alpaca_trading_raw.py`, `adapters/alpaca_broker.py` and
   `cli_record.py`.

`from alpaca.trading import *` fails checks 1 and 2, and a non-literal
`import_module`/`__import__` fails both too, since no static check can follow
it. `adapters/alpaca_broker.py` does not exist yet (T48c); the checks pass on
the current tree and bind the task that adds it.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"

SDK_TRADING = "alpaca.trading"
SDK_CLIENT = "alpaca.trading.client"
SDK_REQUESTS = "alpaca.trading.requests"
CLIENT_IMPORTERS = frozenset(
    {"tradepartner.adapters.alpaca_raw", "tradepartner.adapters.alpaca_trading_raw"}
)
REQUESTS_IMPORTERS = frozenset(
    {"tradepartner.adapters.alpaca_trading_raw", "tradepartner.cli_record"}
)
ORDER_METHOD_USERS = frozenset(
    {
        "tradepartner.adapters.alpaca_trading_raw",
        "tradepartner.adapters.alpaca_broker",
        "tradepartner.cli_record",
    }
)
_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})


@dataclass(frozen=True)
class Module:
    """One parsed source module and its dotted name."""

    name: str
    tree: ast.Module

    @classmethod
    def parse(cls, source: str, name: str) -> Module:
        return cls(name, ast.parse(source))


def _src_modules() -> list[Module]:
    modules = []
    for path in sorted(SRC.rglob("*.py")):
        parts = list(path.relative_to(SRC).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        modules.append(Module.parse(path.read_text(encoding="utf-8"), ".".join(parts)))
    return modules


MODULES = _src_modules()


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)


def _literal_arg(node: ast.Call, index: int) -> str | None:
    if len(node.args) > index:
        arg = node.args[index]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return None


def imported_names(module: Module) -> set[str]:
    """Every dotted name `module` imports: `import a.b` gives `a.b`, and
    `from a import b` gives both `a` and `a.b`. The SDK is third-party, so
    relative imports never reach it and are left unresolved. A literal
    `import_module` counts too."""
    names: set[str] = set()
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and _called_name(node) in _DYNAMIC_IMPORTERS:
            target = _literal_arg(node, 0)
            if target is not None:
                names.add(target)
    return names


def _chain(node: ast.expr) -> str:
    """`a.b.c` for an attribute chain on a name, else the attributes alone."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _attribute_chains(module: Module) -> set[str]:
    return {_chain(node) for node in ast.walk(module.tree) if isinstance(node, ast.Attribute)}


def _strings(module: Module) -> set[str]:
    return {
        node.value
        for node in ast.walk(module.tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def non_literal_dynamic_imports(module: Module) -> list[str]:
    """`import_module(x)`/`__import__(x)` whose target is not a string literal."""
    return [
        f"line {node.lineno}: dynamic import"
        for node in ast.walk(module.tree)
        if isinstance(node, ast.Call)
        and _called_name(node) in _DYNAMIC_IMPORTERS
        and _literal_arg(node, 0) is None
    ]


def _under(name: str, target: str) -> bool:
    return name == target or name.startswith(target + ".")


def _is_trading_chain(chain: str, submodule: str) -> bool:
    """`alpaca.trading.<submodule>` or `trading.<submodule>` inside a chain."""
    parts = chain.split(".")
    return any(parts[i : i + 2] == ["trading", submodule] for i in range(len(parts) - 1))


def client_imports(module: Module) -> list[str]:
    """Imports, attribute reads or name strings of the SDK trading client."""
    found = sorted(
        n
        for n in imported_names(module)
        if _under(n, SDK_CLIENT)
        or n.rpartition(".")[2] == "TradingClient"
        or n == f"{SDK_TRADING}.*"
    )
    found += sorted(
        f"attribute {chain}"
        for chain in _attribute_chains(module)
        if "TradingClient" in chain.split(".") or _is_trading_chain(chain, "client")
    )
    found += sorted(
        f"string {s!r}" for s in _strings(module) if s == "TradingClient" or _under(s, SDK_CLIENT)
    )
    return found + non_literal_dynamic_imports(module)


def requests_imports(module: Module) -> list[str]:
    """Imports or attribute reads of the SDK's order request types."""
    found = sorted(
        n
        for n in imported_names(module)
        if _under(n, SDK_REQUESTS)
        or n == f"{SDK_TRADING}.*"
        or (n.rpartition(".")[0] == SDK_TRADING and n.endswith("Request"))
    )
    found += sorted(
        f"attribute {chain}"
        for chain in _attribute_chains(module)
        if _is_trading_chain(chain, "requests")
    )
    return found + non_literal_dynamic_imports(module)


def _is_order_method(name: object) -> bool:
    return isinstance(name, str) and (name == "submit_order" or name.startswith("cancel_order"))


def order_method_uses(module: Module) -> list[str]:
    """Any `.submit_order`/`.cancel_order*` attribute, called or passed on as
    a bound method, plus `getattr(x, "submit_order")` and
    `methodcaller("submit_order")`, by line. A `def submit_order` is not an
    attribute and does not count."""
    found = []
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Attribute) and _is_order_method(node.attr):
            found.append(f"line {node.lineno}: .{node.attr}")
        elif isinstance(node, ast.Call):
            called = _called_name(node)
            index = 1 if called == "getattr" else 0 if called == "methodcaller" else None
            if index is not None and _is_order_method(_literal_arg(node, index)):
                found.append(f"line {node.lineno}: {called} {_literal_arg(node, index)!r}")
    return found


def _offenders(check: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for module in MODULES:
        if check == "client" and module.name not in CLIENT_IMPORTERS:
            found = client_imports(module)
        elif check == "requests" and module.name not in REQUESTS_IMPORTERS:
            found = requests_imports(module)
        elif check == "order_method" and module.name not in ORDER_METHOD_USERS:
            found = order_method_uses(module)
        else:
            continue
        if found:
            result[module.name] = found
    return result


def test_the_scan_sees_the_sdk_importers() -> None:
    names = {m.name for m in MODULES}
    assert names >= CLIENT_IMPORTERS | REQUESTS_IMPORTERS
    by_name = {m.name: m for m in MODULES}
    assert client_imports(by_name["tradepartner.adapters.alpaca_trading_raw"])
    assert requests_imports(by_name["tradepartner.cli_record"])
    assert order_method_uses(by_name["tradepartner.cli_record"])


@pytest.mark.parametrize("check", ("client", "requests", "order_method"))
def test_sdk_boundary_holds_on_the_tree(check: str) -> None:
    assert _offenders(check) == {}


# The checkers themselves, on synthetic modules.


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from alpaca.trading.client import TradingClient", True),
        ("from alpaca.trading import TradingClient as T", True),
        ("from alpaca.trading import *", True),
        ("import alpaca.trading.client", True),
        ("from alpaca.trading import client", True),
        ("import alpaca\nc = alpaca.trading.client.TradingClient(k, s)", True),
        ("from alpaca import trading\nc = trading.TradingClient(k, s)", True),
        ("importlib.import_module('alpaca.trading.client')", True),
        ("client_type = getattr(module, 'TradingClient')", True),
        ("mod = importlib.import_module(name)", True),
        ("from alpaca.trading.enums import OrderSide", False),
        ("from alpaca.data.historical.stock import StockHistoricalDataClient", False),
        ("from tradepartner.adapters.broker import Broker", False),
    ],
)
def test_client_checker(source: str, expected: bool) -> None:
    assert bool(client_imports(Module.parse(source, "tradepartner.x"))) is expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from alpaca.trading.requests import MarketOrderRequest", True),
        ("import alpaca.trading.requests as r", True),
        ("from alpaca.trading import requests", True),
        ("from alpaca.trading import LimitOrderRequest", True),
        ("from alpaca.trading import *", True),
        ("import alpaca\nr = alpaca.trading.requests.MarketOrderRequest()", True),
        ("__import__('alpaca.trading.requests')", True),
        ("from alpaca.trading.enums import TimeInForce", False),
        ("from alpaca.data.requests import StockBarsRequest", False),
        ("import requests\nrequests.get(url)", False),
    ],
)
def test_requests_checker(source: str, expected: bool) -> None:
    assert bool(requests_imports(Module.parse(source, "tradepartner.x"))) is expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("client.submit_order(request)", True),
        ("client.cancel_order_by_id(order_id)", True),
        ("client.cancel_orders()", True),
        ("raw.cancel_order(order_id)", True),
        ("send = client.submit_order\nsend(request)", True),
        ("functools.partial(client.submit_order, request)", True),
        ("getattr(client, 'submit_order')(request)", True),
        ("getattr(client, 'cancel_orders')()", True),
        ("operator.methodcaller('submit_order', request)(client)", True),
        ("def submit_order(self, request): ...", False),
        ("broker.submit(request)", False),
        ("client.get_orders(filter)", False),
        ("keys = {'submit_order': 1}", False),
    ],
)
def test_order_method_checker(source: str, expected: bool) -> None:
    assert bool(order_method_uses(Module.parse(source, "tradepartner.x"))) is expected

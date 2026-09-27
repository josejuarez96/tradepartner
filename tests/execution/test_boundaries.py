"""Import and call boundaries of the order path (Phase 4 plan T50).

Static checks over `src/tradepartner/`, tests excepted:

1. No module outside `execution/` imports `AlpacaBroker` or `FakeBroker`
   (or the `adapters.alpaca_broker` module), so `cli.py` can only get a
   broker from `execution.brokers.build_broker` (ADR 0003 rule 7).
2. No `.submit(` or `.cancel(` call outside `execution/wrapper.py`, so the
   risk-gated wrapper is the only caller that can place or cancel an order.
3. `adapters.alpaca_trading_raw` is imported only by `adapters/alpaca_broker.py`
   and `cli_record.py` (the owner-run paper recorder, T48).
4. No module other than `store/journal.py` and `store/schema.py` names the
   `fills` table in SQL (`FROM`, `JOIN` or `INTO` followed by the name, or
   the name as a whole string literal), so every reader goes through
   `store.journal.fills_for`. Docstrings, identifiers and module names such
   as a `Broker.fills` method do not count.
5. Nothing under `backtest/` imports `store.journal` or names a journal table
   in SQL by the same rule, so the backtest never reads paper results.

`wrapper.py` does not exist yet; the checks pass on the current tree and bind
the tasks that add it.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from tradepartner.store.schema import JOURNAL_TABLE_NAMES

SRC = Path(__file__).resolve().parents[2] / "src"

BROKER_CLASSES = frozenset({"AlpacaBroker", "FakeBroker"})
ALPACA_BROKER_MODULE = "tradepartner.adapters.alpaca_broker"
TRADING_RAW_MODULE = "tradepartner.adapters.alpaca_trading_raw"
ORDER_CALLS = frozenset({"submit", "cancel"})
WRAPPER = "tradepartner.execution.wrapper"
TRADING_RAW_IMPORTERS = frozenset({ALPACA_BROKER_MODULE, "tradepartner.cli_record"})
FILLS_SQL_OWNERS = frozenset({"tradepartner.store.journal", "tradepartner.store.schema"})
_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})


@dataclass(frozen=True)
class Module:
    """One parsed source module and its dotted name."""

    name: str
    is_package: bool
    tree: ast.Module

    @classmethod
    def parse(cls, source: str, name: str, *, is_package: bool = False) -> Module:
        return cls(name, is_package, ast.parse(source))

    def in_package(self, package: str) -> bool:
        return self.name == package or self.name.startswith(package + ".")


def _src_modules() -> list[Module]:
    modules = []
    for path in sorted(SRC.rglob("*.py")):
        parts = list(path.relative_to(SRC).with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts.pop()
        modules.append(
            Module.parse(path.read_text(encoding="utf-8"), ".".join(parts), is_package=is_package)
        )
    return modules


MODULES = _src_modules()


def _resolve(module: Module, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = module.name if module.is_package else module.name.rpartition(".")[0]
    for _ in range(node.level - 1):
        package = package.rpartition(".")[0]
    return f"{package}.{node.module}" if node.module else package


def imported_names(module: Module) -> set[str]:
    """Every dotted name `module` imports: `import a.b` gives `a.b`, and
    `from a import b` gives both `a` and `a.b` (b may be a module or a name).
    Relative imports are resolved; a literal `import_module` counts too."""
    names: set[str] = set()
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(module, node)
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            first = node.args[0]
            if (
                called in _DYNAMIC_IMPORTERS
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
            ):
                names.add(first.value)
    return names


def _names_module(name: str, target: str) -> bool:
    return name == target or name.startswith(target + ".")


def broker_class_imports(module: Module) -> list[str]:
    """Imports of, or attribute reads of, a concrete broker class."""
    found = sorted(
        name
        for name in imported_names(module)
        if name.rpartition(".")[2] in BROKER_CLASSES or _names_module(name, ALPACA_BROKER_MODULE)
    )
    found += [
        f"attribute {node.attr}"
        for node in ast.walk(module.tree)
        if isinstance(node, ast.Attribute) and node.attr in BROKER_CLASSES
    ]
    return found


def order_calls(module: Module) -> list[str]:
    """`.submit(`/`.cancel(` calls, and `getattr(x, "submit")` look-ups, by line."""
    found = []
    for node in ast.walk(module.tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ORDER_CALLS:
            found.append(f"line {node.lineno}: .{func.attr}(")
        elif (
            isinstance(func, ast.Name)
            and func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in ORDER_CALLS
        ):
            found.append(f"line {node.lineno}: getattr {node.args[1].value!r}")
    return found


def trading_raw_imports(module: Module) -> list[str]:
    return sorted(n for n in imported_names(module) if _names_module(n, TRADING_RAW_MODULE))


def _docstring_nodes(tree: ast.Module) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _strings(tree: ast.Module) -> list[str]:
    """Every string constant, f-string parts included, docstrings excluded."""
    docstrings = _docstring_nodes(tree)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def sql_table_references(module: Module, tables: tuple[str, ...]) -> list[str]:
    """Tables among `tables` that `module` names in SQL or as a whole literal."""
    found = []
    strings = _strings(module.tree)
    for table in tables:
        pattern = re.compile(
            rf"\b(?:FROM|JOIN|INTO)\s+(?:\w+\.)?[\"'`]?{re.escape(table)}\b", re.IGNORECASE
        )
        quoted = {table, f'"{table}"', f"'{table}'", f"`{table}`"}
        if any(s.strip() in quoted or pattern.search(s) for s in strings):
            found.append(table)
    return found


def backtest_journal_references(module: Module) -> list[str]:
    """`store.journal` imports and journal tables named in SQL."""
    journal = [n for n in imported_names(module) if _names_module(n, "tradepartner.store.journal")]
    return sorted(journal) + sql_table_references(module, JOURNAL_TABLE_NAMES)


def _offenders(check: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for module in MODULES:
        if check == "broker_class" and not module.in_package("tradepartner.execution"):
            found = broker_class_imports(module)
        elif check == "order_call" and module.name != WRAPPER:
            found = order_calls(module)
        elif check == "trading_raw" and module.name not in TRADING_RAW_IMPORTERS:
            found = trading_raw_imports(module)
        elif check == "fills_sql" and module.name not in FILLS_SQL_OWNERS:
            found = sql_table_references(module, ("fills",))
        elif check == "backtest" and module.in_package("tradepartner.backtest"):
            found = backtest_journal_references(module)
        else:
            continue
        if found:
            result[module.name] = found
    return result


def test_the_scan_sees_the_source_tree() -> None:
    names = {m.name for m in MODULES}
    assert {"tradepartner.adapters.broker", "tradepartner.store.journal"} <= names
    assert any(m.in_package("tradepartner.backtest") for m in MODULES)


CHECKS = ("broker_class", "order_call", "trading_raw", "fills_sql", "backtest")


@pytest.mark.parametrize("check", CHECKS)
def test_boundary_holds_on_the_tree(check: str) -> None:
    assert _offenders(check) == {}


# The checkers themselves, on synthetic modules.


@pytest.mark.parametrize(
    ("source", "name", "is_package", "expected"),
    [
        ("from tradepartner.adapters.broker import FakeBroker", "tradepartner.cli", False, True),
        ("from tradepartner.adapters.broker import Broker", "tradepartner.cli", False, False),
        ("from .broker import FakeBroker as F", "tradepartner.adapters.other", False, True),
        ("from ..adapters.alpaca_broker import AlpacaBroker", "tradepartner.cli.x", False, True),
        ("from tradepartner.adapters import alpaca_broker", "tradepartner.cli", False, True),
        ("import tradepartner.adapters.alpaca_broker", "tradepartner.cli", False, True),
        ("from . import alpaca_broker", "tradepartner.adapters", True, True),
        ("from tradepartner.adapters import broker\nb = broker.FakeBroker", "t.cli", False, True),
        ("importlib.import_module('tradepartner.adapters.alpaca_broker')", "t.x", False, True),
        ("import tradepartner.adapters.broker", "tradepartner.cli", False, False),
    ],
)
def test_broker_class_checker(source: str, name: str, is_package: bool, expected: bool) -> None:
    module = Module.parse(source, name, is_package=is_package)
    assert bool(broker_class_imports(module)) is expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("broker.submit(request)", True),
        ("self._broker.cancel(order_id)", True),
        ("getattr(broker, 'submit')(request)", True),
        ("client.submit_order(payload)", False),
        ("broker.get_order(order_id)", False),
        ("def submit(self, request): ...", False),
    ],
)
def test_order_call_checker(source: str, expected: bool) -> None:
    assert bool(order_calls(Module.parse(source, "tradepartner.x"))) is expected


@pytest.mark.parametrize(
    ("source", "name", "expected"),
    [
        ("from tradepartner.adapters import alpaca_trading_raw", "tradepartner.cli", True),
        ("from tradepartner.adapters.alpaca_trading_raw import AlpacaTradingRaw", "t.x", True),
        ("from .alpaca_trading_raw import AlpacaTradingRaw", "tradepartner.adapters.y", True),
        ("import tradepartner.adapters.alpaca_trading_raw as raw", "tradepartner.x", True),
        ("from tradepartner.adapters import alpaca_raw", "tradepartner.cli", False),
    ],
)
def test_trading_raw_checker(source: str, name: str, expected: bool) -> None:
    assert bool(trading_raw_imports(Module.parse(source, name))) is expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('conn.execute("SELECT * FROM fills")', True),
        ('conn.execute("select f.x from main.fills f")', True),
        ("conn.execute('SELECT 1 FROM orders o JOIN \"fills\" f USING (id)')", True),
        ('conn.execute("INSERT INTO fills VALUES (?)")', True),
        ('table = "fills"', True),
        ('conn.execute(f"SELECT * FROM {t} WHERE x = 1", t="fills")', True),
        ('"""Positions aggregated from fills."""', False),
        ('def f():\n    """Read from fills."""\n    return broker.fills(since)', False),
        ('conn.execute("SELECT * FROM fill_cursors")', False),
        ("from tradepartner.backtest import fills", False),
        ('label = "fills per day"', False),
    ],
)
def test_fills_sql_checker(source: str, expected: bool) -> None:
    found = sql_table_references(Module.parse(source, "tradepartner.x"), ("fills",))
    assert bool(found) is expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from tradepartner.store import journal", True),
        ("from tradepartner.store.journal import fills_for", True),
        ("from ..store.journal import OrderRow", True),
        ('conn.execute("SELECT * FROM signals")', True),
        ('conn.execute("SELECT * FROM decisions")', True),
        ("from tradepartner.backtest import signals", False),
        ("from tradepartner.store import registry", False),
        ('"""Rows read from decisions."""', False),
    ],
)
def test_backtest_checker(source: str, expected: bool) -> None:
    module = Module.parse(source, "tradepartner.backtest.engine")
    assert bool(backtest_journal_references(module)) is expected

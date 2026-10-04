"""Import and call boundaries of the order path (Phase 4 plan T50).

Static checks over `src/tradepartner/`, tests excepted:

1. No module outside `execution/` imports, reads as an attribute or names in
   a string `AlpacaBroker` or `FakeBroker`, or imports the
   `adapters.alpaca_broker` or `adapters.fake_broker` module, so `cli.py` can
   only get a broker from `execution.brokers.build_broker` (ADR 0003 rule 7).
2. No `.submit` or `.cancel` attribute, called or passed on as a bound method,
   and no `getattr`/`methodcaller`/`attrgetter`/`__getattribute__` with those
   names, outside `execution/wrapper.py`, so the risk-gated wrapper is the only
   code that can place or cancel an order. Those lookups with a non-literal name
   are refused too (`getattr` only when called on the spot, outside
   `NON_LITERAL_GETATTR_CALLERS`). An unrelated `executor.submit` is refused
   too, by intent. A lookup function renamed on import (`from builtins import
   getattr as g`) is resolved back to its real name first; a bound
   `__getattribute__` read but not called on the spot is refused outright
   (it may be called with any name later); an order name subscripted out of
   `__dict__` or `vars()` is refused too (#471).
3. `adapters.alpaca_trading_raw` and `AlpacaTradingRaw` are imported or read
   only by `adapters/alpaca_broker.py` and `cli_record.py` (the owner-run paper
   recorder, T48), re-exports and attribute chains included.
4. No module other than `store/journal.py` and `store/schema.py` names the
   `fills` table in SQL (after `FROM`, `JOIN`, `INTO`, `UPDATE`, `TABLE` or
   `USING`, parenthesised or not, or in a comma-separated `FROM` list after
   `SELECT`, `UPDATE` or `DELETE` or after an upper-case `FROM`, optionally
   schema-qualified and quoted), as
   a whole string literal, or through `FillRow.TABLE`, so every reader goes
   through `store.journal.fills_for`. Docstrings, identifiers and module names
   such as a `Broker.fills` method do not count.
5. Nothing under `backtest/` imports `store.journal` or `execution` (attribute
   chains through import aliases included), reads
   `JOURNAL_TABLE_NAMES`, or names a journal table in SQL by the same rule,
   so the backtest never reads paper results.

A non-literal `import_module`/`__import__` fails checks 1 and 3, since no
static check can follow it.

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
BROKER_MODULES = (ALPACA_BROKER_MODULE, "tradepartner.adapters.fake_broker")
TRADING_RAW_MODULE = "tradepartner.adapters.alpaca_trading_raw"
ORDER_CALLS = frozenset({"submit", "cancel"})
WRAPPER = "tradepartner.execution.wrapper"
TRADING_RAW_IMPORTERS = frozenset({ALPACA_BROKER_MODULE, "tradepartner.cli_record"})
TRADING_RAW_NAMES = frozenset({"AlpacaTradingRaw", "alpaca_trading_raw"})
FILLS_SQL_OWNERS = frozenset({"tradepartner.store.journal", "tradepartner.store.schema"})
_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})
# Modules allowed to call `getattr(obj, name)(...)` with a non-literal name (#418). Each
# entry says why; literal order names stay refused there too.
NON_LITERAL_GETATTR_CALLERS = frozenset(
    {
        "tradepartner.ingest",  # `_Recorded._ask`: memoising proxy over a FilingSource
    }
)


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


def _chain(node: ast.expr) -> str:
    """`a.b.c` for an attribute chain on a name, else the last attribute."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _attribute_chains(module: Module) -> set[str]:
    return {_chain(node) for node in ast.walk(module.tree) if isinstance(node, ast.Attribute)}


def _import_aliases(module: Module) -> dict[str, str]:
    """Local names bound by imports, mapped to the dotted name they stand for:
    `import a.b as c` gives `c -> a.b`, `from a import b as c` gives `c -> a.b`."""
    aliases: dict[str, str] = {}
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Import):
            aliases.update({a.asname: a.name for a in node.names if a.asname})
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(module, node)
            aliases.update({a.asname or a.name: f"{base}.{a.name}" for a in node.names})
    return aliases


def _resolved_chains(module: Module) -> set[str]:
    """Attribute chains, plus each one with its head resolved through the
    module's import aliases (`from tradepartner import store; store.journal`
    gives `tradepartner.store.journal` too, #406)."""
    aliases = _import_aliases(module)
    chains = _attribute_chains(module)
    resolved = set(chains)
    for chain in chains:
        head, dot, rest = chain.partition(".")
        if head in aliases:
            resolved.add(aliases[head] + dot + rest)
    return resolved


def _called_name(node: ast.Call) -> str | None:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)


def non_literal_dynamic_imports(module: Module) -> list[str]:
    """`import_module(x)`/`__import__(x)` whose target is not a string literal,
    which no static check can follow."""
    return [
        f"line {node.lineno}: dynamic import"
        for node in ast.walk(module.tree)
        if isinstance(node, ast.Call)
        and _called_name(node) in _DYNAMIC_IMPORTERS
        and not (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        )
    ]


def broker_class_imports(module: Module) -> list[str]:
    """Imports, attribute reads or name strings of a concrete broker class."""
    found = sorted(
        name
        for name in imported_names(module)
        if name.rpartition(".")[2] in BROKER_CLASSES
        or any(_names_module(name, target) for target in BROKER_MODULES)
    )
    found += sorted(
        f"attribute {chain}"
        for chain in _attribute_chains(module)
        if chain.rpartition(".")[2] in BROKER_CLASSES
    )
    found += sorted({f"string {s!r}" for s in _strings(module.tree) if s in BROKER_CLASSES})
    return found + non_literal_dynamic_imports(module)


def _resolved_called_name(aliases: dict[str, str], node: ast.Call) -> str | None:
    """`_called_name`, with a bare name resolved through import aliases to the
    dotted target's last component when it renames one of the by-name lookup
    functions (`from builtins import getattr as g`, `from operator import
    attrgetter as ag`, #471). An attribute call's name is already the real
    one, whatever its module is imported as."""
    called = _called_name(node)
    if isinstance(node.func, ast.Name) and called in aliases:
        resolved = aliases[called].rpartition(".")[2]
        if resolved in ("getattr", "methodcaller", "attrgetter", "__getattribute__"):
            return resolved
    return called


def _name_args(node: ast.Call, called: str | None) -> list[ast.expr]:
    """The arguments that name an attribute in a by-name lookup call."""
    if any(isinstance(a, ast.Starred) for a in node.args) and called in (
        "getattr",
        "methodcaller",
        "attrgetter",
        "__getattribute__",
    ):
        return list(node.args)  # the name may be inside the star: never a literal
    if called == "getattr":
        return node.args[1:2]
    if called == "methodcaller":
        return node.args[:1]
    if called in ("attrgetter", "__getattribute__"):
        return list(node.args)
    return []


def order_calls(module: Module) -> list[str]:
    """Any `.submit`/`.cancel` attribute, called or passed on as a bound method,
    plus a by-name lookup of those names (`getattr`, `methodcaller`, `attrgetter`,
    dotted paths included, and `__getattribute__`), by line. A lookup whose name
    is not a string literal is refused, since no static check can follow it
    (#418); for `getattr` only when its result is called on the spot, because
    `getattr(obj, field_name)` field reads are common and harmless. A lookup
    function renamed on import (`from builtins import getattr as g`) is resolved
    back to its real name first (#471). A bound `__getattribute__` that is read
    but not called on the spot is refused outright, since it may be called with
    any name later and no static check can follow it; so is an order name
    subscripted out of `__dict__` or `vars()` (#471)."""
    aliases = _import_aliases(module)
    called_on_the_spot = {
        id(node.func) for node in ast.walk(module.tree) if isinstance(node, ast.Call)
    }
    found = []
    for node in ast.walk(module.tree):
        if isinstance(node, ast.Attribute) and node.attr in ORDER_CALLS:
            found.append(f"line {node.lineno}: .{node.attr}")
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == "__getattribute__"
            and id(node) not in called_on_the_spot
        ):
            found.append(f"line {node.lineno}: stored __getattribute__")
        elif isinstance(node, ast.Subscript):
            key = node.slice
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and key.value in ORDER_CALLS
            ):
                value = node.value
                via_dict = isinstance(value, ast.Attribute) and value.attr == "__dict__"
                via_vars = isinstance(value, ast.Call) and _called_name(value) == "vars"
                if via_dict or via_vars:
                    found.append(f"line {node.lineno}: {key.value!r} via __dict__/vars()")
        elif isinstance(node, ast.Call) and (
            args := _name_args(node, called := _resolved_called_name(aliases, node))
        ):
            literals = [
                a.value for a in args if isinstance(a, ast.Constant) and isinstance(a.value, str)
            ]
            # a literal order name is flagged first, whatever else the call passes
            if any(ORDER_CALLS & set(name.split(".")) for name in literals):
                found.append(f"line {node.lineno}: {called} {literals!r}")
            else:
                # `object.__getattribute__(obj, "x")` passes the object too: one literal will do
                non_literal = not literals or (
                    called != "__getattribute__" and len(literals) < len(args)
                )
                refused = called != "getattr" or (
                    id(node) in called_on_the_spot
                    and module.name not in NON_LITERAL_GETATTR_CALLERS
                )
                if non_literal and refused:
                    found.append(f"line {node.lineno}: {called} with a non-literal name")
    return found


def trading_raw_imports(module: Module) -> list[str]:
    """Imports or attribute reads of the raw trading client or its module,
    re-exports included (`from tradepartner.cli_record import AlpacaTradingRaw`)."""
    found = sorted(
        n
        for n in imported_names(module)
        if _names_module(n, TRADING_RAW_MODULE) or n.rpartition(".")[2] in TRADING_RAW_NAMES
    )
    found += sorted(
        f"attribute {chain}"
        for chain in _attribute_chains(module)
        if TRADING_RAW_NAMES & set(chain.split("."))
    )
    return found + non_literal_dynamic_imports(module)


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


#: An optional, optionally quoted schema qualifier, then an optional quote.
_QUALIFIER = r"(?:[\"'`]?\w+[\"'`]?\.)?[\"'`]?"


def sql_table_references(module: Module, tables: tuple[str, ...]) -> list[str]:
    """Tables among `tables` that `module` names in SQL (after `FROM`, `JOIN`,
    `INTO`, `UPDATE`, `TABLE` or `USING`, parenthesised or not, or in a
    comma-separated `FROM` list after `SELECT`, `UPDATE`, `DELETE` or an
    upper-case `FROM`) or as a whole string literal."""
    found = []
    strings = _strings(module.tree)
    for table in tables:
        name = re.escape(table)
        # `FROM (fills)` and `FROM(fills)` count (#407); `DELETE ... USING fills` too (#471)
        keyword = re.compile(
            rf"\b(?:FROM|JOIN|INTO|UPDATE|TABLE|USING)(?=[\s(])[\s(]*{_QUALIFIER}{name}\b",
            re.IGNORECASE,
        )
        # a comma-separated FROM list: any case after SELECT, UPDATE or DELETE, and an
        # upper-case FROM anywhere (a query fragment), so lower-case prose such as "from
        # orders, fills and adjustments" does not count (#407)
        listed = rf"\bFROM\b[^;]*?,[\s(]*{_QUALIFIER}{name}\b"
        from_list = re.compile(
            rf"\b(?:SELECT|UPDATE|DELETE)\b[^;]*?{listed}|(?-i:{listed})", re.IGNORECASE
        )
        quoted = {table, f'"{table}"', f"'{table}'", f"`{table}`"}
        if any(s.strip() in quoted or keyword.search(s) or from_list.search(s) for s in strings):
            found.append(table)
    return found


def fills_table_references(module: Module) -> list[str]:
    """The `fills` table in SQL, or read through `FillRow.TABLE`. Building a
    `FillRow` for `store.journal.append` stays allowed."""
    found = sql_table_references(module, ("fills",))
    found += sorted(
        f"attribute {chain}"
        for chain in _attribute_chains(module)
        if chain == "FillRow.TABLE" or chain.endswith(".FillRow.TABLE")
    )
    return found


_BACKTEST_FORBIDDEN = ("tradepartner.store.journal", "tradepartner.execution")


def backtest_journal_references(module: Module) -> list[str]:
    """`store.journal` or `execution` imports and attribute chains (through
    import aliases too), any `JOURNAL_TABLE_NAMES` reference, and journal tables
    named in SQL."""
    names = imported_names(module) | _resolved_chains(module)
    found = sorted(
        n for n in names if any(_names_module(n, target) for target in _BACKTEST_FORBIDDEN)
    )
    found += sorted(n for n in names if n.rpartition(".")[2] == "JOURNAL_TABLE_NAMES")
    found += [
        f"name {node.id}"
        for node in ast.walk(module.tree)
        if isinstance(node, ast.Name) and node.id == "JOURNAL_TABLE_NAMES"
    ]
    return found + sql_table_references(module, JOURNAL_TABLE_NAMES)


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
            found = fills_table_references(module)
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
        ("broker_type = getattr(module, 'FakeBroker')", "tradepartner.cli", False, True),
        ("from tradepartner.adapters.fake_broker import *\nb = FakeBroker()", "t.cli", False, True),
        ("importlib.import_module('tradepartner.adapters.fake_broker')", "t.cli", False, True),
        ("mod = importlib.import_module(name)", "tradepartner.cli", False, True),
        ("mod = __import__(f'tradepartner.adapters.{n}')", "tradepartner.cli", False, True),
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
        ("send = broker.submit\nsend(request)", True),
        ("functools.partial(broker.submit, request)", True),
        ("list(map(broker.submit, requests))", True),
        ("retry(broker.cancel, order_id)", True),
        ("operator.methodcaller('submit', request)(broker)", True),
        ("keys = {'submit': 1, 'cancel': 2}", False),
        # #418: attrgetter, a non-literal name, and __getattribute__
        ("operator.attrgetter('submit')(broker)(request)", True),
        ("attrgetter('order.cancel')(self)(order_id)", True),
        ("name = 'submit'\ngetattr(broker, name)(request)", True),
        ("getattr(broker, f'sub{x}')(request)", True),
        ("operator.methodcaller(name, request)(broker)", True),
        ("operator.attrgetter(name)(broker)", True),
        ("broker.__getattribute__('submit')(request)", True),
        ("object.__getattribute__(broker, 'cancel')(order_id)", True),
        ("broker.__getattribute__(name)(request)", True),
        ("getattr(func, 'id', None)", False),
        ("value = getattr(record, field_name)", False),
        ("getattr(*target)(request)", True),
        ('m = getattr(*(broker,), "submit")', True),
        ("operator.attrgetter('price')(row)", False),
        ("row.__getattribute__('price')", False),
        # #471: renamed builtins resolved through import aliases
        ("from builtins import getattr as g\ng(broker, 'submit')(request)", True),
        ("from operator import attrgetter as ag\nag('cancel')(broker)(order_id)", True),
        ("from operator import attrgetter as ag\nag('price')(row)", False),
        ("from builtins import getattr as g\nvalue = g(record, field_name)", False),
        # #471: a bound __getattribute__ stored for later use, not called on the spot
        ("ga = broker.__getattribute__\nga('submit')(request)", True),
        ("peek = row.__getattribute__", True),
        ("broker.__getattribute__('submit')(request)", True),
        # #471: __dict__/vars() subscripted with an order name
        ("type(broker).__dict__['submit'](broker, request)", True),
        ("vars(type(broker))['cancel']", True),
        ("type(broker).__dict__['price']", False),
        ("vars(row)['count']", False),
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
        ("from tradepartner.cli_record import AlpacaTradingRaw", "tradepartner.cli", True),
        ("from tradepartner.adapters.alpaca_broker import AlpacaTradingRaw", "t.execution.x", True),
        (
            "import tradepartner.adapters\n"
            "tradepartner.adapters.alpaca_trading_raw.AlpacaTradingRaw()",
            "tradepartner.cli",
            True,
        ),
        ("mod = importlib.import_module(name)", "tradepartner.cli", True),
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
        ('conn.execute("SELECT * FROM orders o, fills f WHERE o.id = f.id")', True),
        ('conn.execute("UPDATE fills SET superseded_by = 1")', True),
        ('conn.execute(\'SELECT * FROM "main"."fills"\')', True),
        ('conn.execute(f"SELECT * FROM {FillRow.TABLE}")', True),
        ("insert_row(conn, journal.FillRow.TABLE, values)", True),
        ("append(conn, FillRow(broker_fill_id='x'))", False),
        # #407: a parenthesised table, and prose with a comma list
        ('conn.execute("SELECT * FROM (fills)")', True),
        ('conn.execute("SELECT * FROM ( fills ) f")', True),
        ('conn.execute("SELECT * FROM(fills)")', True),
        ('conn.execute("SELECT * FROM orders o, (fills) f")', True),
        ('reason = "positions from orders, fills and adjustments"', False),
        ('conn.execute("UPDATE orders o SET x = 1 FROM orders p, fills f WHERE 1")', True),
        ('where = "FROM orders, fills"', True),
        ('conn.execute("delete from orders using x from y, fills")', True),
        # #471: DELETE ... USING fills, with no comma-separated FROM list
        ('conn.execute("DELETE FROM orders USING fills WHERE orders.id = fills.id")', True),
        ('conn.execute("delete from orders using fills where orders.id = fills.id")', True),
    ],
)
def test_fills_sql_checker(source: str, expected: bool) -> None:
    assert bool(fills_table_references(Module.parse(source, "tradepartner.x"))) is expected


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
        ("from tradepartner.execution.ledger import from_journal", True),
        ("import tradepartner.store\ntradepartner.store.journal.fills_for(conn)", True),
        ("from tradepartner.store.schema import JOURNAL_TABLE_NAMES", True),
        ("from tradepartner.store import schema\nnames = schema.JOURNAL_TABLE_NAMES", True),
        ("from tradepartner.store.schema import TABLE_NAMES", False),
        # #406: attribute chains through an aliased parent-package import
        ("from tradepartner import store\nstore.journal.fills_for(conn)", True),
        ("from tradepartner import store as s\ns.journal.fills_for(conn)", True),
        ("import tradepartner.store as st\nst.journal.fills_for(conn)", True),
        ("from .. import store\nstore.journal.fills_for(conn)", True),
        ("import tradepartner as tp\ntp.execution.ledger.from_journal(x)", True),
        ("from tradepartner import store\nstore.registry.trials(conn)", False),
        ("import tradepartner.store as st\nst.registry.trials(conn)", False),
    ],
)
def test_backtest_checker(source: str, expected: bool) -> None:
    module = Module.parse(source, "tradepartner.backtest.engine")
    assert bool(backtest_journal_references(module)) is expected

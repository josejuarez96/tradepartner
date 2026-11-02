"""New statement names stay out of trials (#2826, T366; owner answer 4).

`registry._TRIAL_TABLE_ROWS["statement_facts"]` counts only the eight names of
`PRE_2826_STATEMENT_FACTS`, so a backfill of a new name stales no registered
trial. That is exact only while no trial reads a new name, so this source scan
holds the readers to it:

- no module under `backtest/`, `research/` or `universe.py` names a fact
  outside the constant as a string literal, and
- every function there that reads `statement_facts` (the `statement_facts_as_of`
  call or a `FROM statement_facts` query) filters `fact_name` on a named
  constant, and that constant lies inside `PRE_2826_STATEMENT_FACTS`.

A reader that opts into a new name must widen the vintage rule in the same
change; until then this test fails it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tradepartner.backtest import provider
from tradepartner.config_edgar import _STATEMENT_TAGS, PRE_2826_STATEMENT_FACTS

_SRC = Path(__file__).resolve().parents[2] / "src" / "tradepartner"
_READ_SQL = re.compile(r"\b(?:FROM|JOIN)\s+statement_facts\b", re.IGNORECASE)
_HELD = frozenset(PRE_2826_STATEMENT_FACTS)
#: A name the backfill will add, so the scan bites before T365 lands it in the config.
_EXAMPLE_NEW = frozenset(
    {"eps_diluted", "eps_basic", "net_income", "operating_income", "total_liabilities"}
)


def _scanned() -> list[Path]:
    files = sorted((_SRC / "backtest").rglob("*.py")) + sorted((_SRC / "research").rglob("*.py"))
    return [*files, _SRC / "universe.py"]


def _new_names() -> frozenset[str]:
    """Every statement name the config knows or could, outside the held eight."""
    return frozenset(_STATEMENT_TAGS) - _HELD


def literal_new_names(source: str, new_names: frozenset[str]) -> list[str]:
    """The string literals in `source` that equal a new statement name."""
    return sorted(
        {
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and node.value in new_names
        }
    )


#: The constants a reader may filter on; each is checked to lie inside the held names.
_ALLOWED_CONSTANTS = frozenset({"STATEMENT_FACT_NAMES", "PRE_2826_STATEMENT_FACTS"})


def _is_named_constant(node: ast.expr) -> bool:
    name = node.id if isinstance(node, ast.Name) else getattr(node, "attr", "")
    return name in _ALLOWED_CONSTANTS


def _filters_fact_name(func: ast.AST) -> bool:
    """Whether `func` has `<...col("fact_name")...>.is_in(<named constant>)` or a
    SQL `fact_name IN` string."""
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.search(r"\bfact_name\s+IN\b", node.value, re.IGNORECASE)
        ):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "is_in"
            and len(node.args) == 1
            and _is_named_constant(node.args[0])
            and any(
                isinstance(n, ast.Constant) and n.value == "fact_name"
                for n in ast.walk(node.func.value)
            )
        ):
            return True
    return False


def _reads_statement_facts(func: ast.AST) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            callee = node.func
            name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
            if name == "statement_facts_as_of":
                return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _READ_SQL.search(node.value)
        ):
            return True
    return False


def unfiltered_readers(source: str) -> list[str]:
    """The functions in `source` that read `statement_facts` without filtering
    `fact_name` on a named constant. A module-level read counts as one reader."""
    tree = ast.parse(source)
    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    bad = [f.name for f in funcs if _reads_statement_facts(f) and not _filters_fact_name(f)]
    top = ast.Module(
        body=[n for n in tree.body if not isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)],
        type_ignores=[],
    )
    if _reads_statement_facts(top) and not _filters_fact_name(top):
        bad.append("<module>")
    return bad


def test_no_scanned_module_names_a_new_statement_name() -> None:
    for path in _scanned():
        assert literal_new_names(path.read_text(), _new_names() | _EXAMPLE_NEW) == [], path


def test_every_scanned_statement_read_filters_fact_name_on_a_named_constant() -> None:
    readers = 0
    for path in _scanned():
        source = path.read_text()
        assert unfiltered_readers(source) == [], path
        readers += "statement_facts_as_of(" in source
    assert readers >= 1  # the scan is not vacuous: store_provider reads it


def test_the_provider_constant_lies_inside_the_held_names() -> None:
    assert set(provider.STATEMENT_FACT_NAMES) <= _HELD


def test_the_scan_fails_on_a_planted_literal_reader() -> None:
    planted = 'def read(frame):\n    return frame.filter(frame["fact_name"] == "eps_diluted")\n'
    assert literal_new_names(planted, frozenset({"eps_diluted"})) == ["eps_diluted"]
    assert literal_new_names('X = "revenue"\n', frozenset({"eps_diluted"})) == []


def test_the_scan_fails_on_a_planted_unfiltered_read() -> None:
    unfiltered = "def read(c, t):\n    return statement_facts_as_of(c, t)\n"
    assert unfiltered_readers(unfiltered) == ["read"]
    sql = 'def read(c):\n    return c.execute("SELECT * FROM statement_facts")\n'
    assert unfiltered_readers(sql) == ["read"]
    literal_filter = (
        "def read(c, t):\n"
        "    f = statement_facts_as_of(c, t)\n"
        '    return f.filter(pl.col("fact_name").is_in(["gross_profit"]))\n'
    )
    assert unfiltered_readers(literal_filter) == ["read"]
    named = (
        "def read(c, t):\n"
        "    f = statement_facts_as_of(c, t)\n"
        '    return f.filter(pl.col("fact_name").is_in(STATEMENT_FACT_NAMES))\n'
    )
    assert unfiltered_readers(named) == []
    other = named.replace("STATEMENT_FACT_NAMES", "NEW_NAMES")
    assert unfiltered_readers(other) == ["read"]
    join = 'def read(c):\n    return c.execute("SELECT * FROM x JOIN statement_facts f ON 1")\n'
    assert unfiltered_readers(join) == ["read"]

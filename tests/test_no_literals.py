"""AST check: the universe and survivorship-gap modules hold no numeric
literal other than 0, 1 and -1 (spec req 14 acceptance; plan T14).

Every threshold in them comes from `settings.universe` / `settings.gap`.
`-1` parses as a unary minus on the constant `1`, so it needs no entry.
`gap.py` is T15's; its case skips while the module is absent and T15's
plan box is open, and fails if the box is ticked but the module is not at
the planned path.

The Phase 4 execution modules that hold risk limits and money arithmetic
(`execution/{risk,ids,reconcile,ledger,lots,plan}.py`, Phase 4 plan T50,
and `execution/{phases,reattempts,marks,planning,exits,reserve}.py`, T60c;
ADR 0010: no risk limit is a literal in code) allow 0, 1, -1 and 2, plus, in
`lots.py` only, the single positive integer assigned to the one
`WASH_SALE_WINDOW_DAYS` constant (IRC section 1091). Each task that writes
one of these modules relies on this file and never edits it; a module still
absent is skipped while its task's plan box is open.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "tradepartner"
PLAN = ROOT / "docs" / "plans" / "data-foundation.md"
PAPER_PLAN = ROOT / "docs" / "plans" / "paper-trading.md"
ALLOWED = {0, 1}
EXECUTION_ALLOWED = {0, 1, 2}
NAMED_CONSTANT = "WASH_SALE_WINDOW_DAYS"
NAMED_CONSTANT_MODULE = "lots.py"
#: Execution module -> the Phase 4 plan task that writes it.
EXECUTION_MODULES = {
    "ids.py": "T50",
    "ledger.py": "T51",
    "plan.py": "T52",
    "risk.py": "T54",
    "reconcile.py": "T55",
    "lots.py": "T56",
    # The pure wrapper and run modules (plan T60c, 2026-09-30, #388; reserve.py #469).
    "phases.py": "T60c",
    "reattempts.py": "T60d",
    "marks.py": "T63b",
    "planning.py": "T63c",
    "exits.py": "T63e",
    "reserve.py": "T54d",
}


def numeric_literals(source: str) -> set[int | float | complex]:
    """Every numeric constant in `source`, booleans excluded."""
    return {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int | float | complex)
        and not isinstance(node.value, bool)
    }


def _named_constant_values(tree: ast.AST) -> tuple[int, set[int]]:
    """How many assignments bind `NAMED_CONSTANT`, and the ids of the
    right-hand sides that are a single positive `int` constant (anything
    else, such as a float or a dict, stays subject to the check)."""
    count, nodes = 0, set()
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        if value is not None and any(
            isinstance(t, ast.Name) and t.id == NAMED_CONSTANT for t in targets
        ):
            count += 1
            if isinstance(value, ast.Constant) and type(value.value) is int and value.value > 0:
                nodes.add(id(value))
    return count, nodes


def execution_literals(source: str, *, named_constant: bool = True) -> set[int | float | complex]:
    """Numeric constants outside the one `WASH_SALE_WINDOW_DAYS` assignment;
    a second assignment of that name is refused. With `named_constant`
    false (every module but `lots.py`) nothing is exempt."""
    tree = ast.parse(source)
    count, exempt = _named_constant_values(tree)
    assert count <= 1, f"{NAMED_CONSTANT} is assigned {count} times"
    if not named_constant:
        exempt = set()
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int | float | complex)
        and not isinstance(node.value, bool)
        and id(node) not in exempt
    }


@pytest.mark.parametrize("module", sorted(EXECUTION_MODULES))
def test_execution_module_has_no_numeric_literals_but_0_1_2_minus_1(module: str) -> None:
    path = SRC / "execution" / module
    task = EXECUTION_MODULES[module]
    if not path.exists():
        assert f"- [x] **{task}:" not in PAPER_PLAN.read_text(), (
            f"{task} is done but execution/{module} is missing"
        )
        pytest.skip(f"execution/{module} lands with {task}")
    literals = execution_literals(path.read_text(), named_constant=module == NAMED_CONSTANT_MODULE)
    assert literals <= EXECUTION_ALLOWED


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("x = 2\ny = -1", {2, 1}),
        ("WASH_SALE_WINDOW_DAYS = 30\nz = WASH_SALE_WINDOW_DAYS + 1", {1}),
        ("WASH_SALE_WINDOW_DAYS: int = 30", set()),
        ("OTHER_DAYS = 30", {30}),
        ("limit = 0.05", {0.05}),
        ("WASH_SALE_WINDOW_DAYS = 0.05", {0.05}),
        ("WASH_SALE_WINDOW_DAYS = {'w': 0.05}", {0.05}),
        ("WASH_SALE_WINDOW_DAYS = 30 + 5", {30, 5}),
    ],
)
def test_execution_literals_exempts_only_the_named_constant(source: str, found: set[float]) -> None:
    assert execution_literals(source) == found


def test_named_constant_is_exempt_only_in_lots() -> None:
    assert execution_literals("WASH_SALE_WINDOW_DAYS = 30", named_constant=False) == {30}


def test_execution_literals_refuses_a_second_named_assignment() -> None:
    with pytest.raises(AssertionError, match=NAMED_CONSTANT):
        execution_literals("WASH_SALE_WINDOW_DAYS = 30\nWASH_SALE_WINDOW_DAYS = 31")


@pytest.mark.parametrize("module", ["universe.py", "gap.py"])
def test_module_has_no_numeric_literals_but_0_1_minus_1(module: str) -> None:
    path = SRC / module
    if module == "gap.py" and not path.exists():
        assert "- [x] **T15:" not in PLAN.read_text(), "T15 is done but gap.py is missing"
        pytest.skip("gap.py lands with T15")
    assert numeric_literals(path.read_text()) <= ALLOWED


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("x = 0\ny = x - 1\nz = -1", {0, 1}),
        ("flag = True", set()),
        ("limit = 5", {5}),
        ("scale = 1e6", {1e6}),
        ("ratio = 0.5", {0.5}),
        ("items[2]", {2}),
    ],
)
def test_numeric_literals_finds_each_constant(source: str, found: set[float]) -> None:
    assert numeric_literals(source) == found

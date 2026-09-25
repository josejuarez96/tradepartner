"""AST check: the universe and survivorship-gap modules hold no numeric
literal other than 0, 1 and -1 (spec req 14 acceptance; plan T14).

Every threshold in them comes from `settings.universe` / `settings.gap`.
`-1` parses as a unary minus on the constant `1`, so it needs no entry.
`gap.py` is T15's; its case skips while the module is absent and T15's
plan box is open, and fails if the box is ticked but the module is not at
the planned path.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "tradepartner"
PLAN = ROOT / "docs" / "plans" / "data-foundation.md"
ALLOWED = {0, 1}


def numeric_literals(source: str) -> set[int | float | complex]:
    """Every numeric constant in `source`, booleans excluded."""
    return {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int | float | complex)
        and not isinstance(node.value, bool)
    }


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

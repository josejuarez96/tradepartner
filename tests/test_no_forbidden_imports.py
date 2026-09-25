"""`bt` is a test-only oracle (ADR 0004): nothing under `src/` imports it.

`ffn` and `yfinance` come in with `bt` as transitive dev dependencies; they
stay out of runtime code too (ADR 0004 avoids `yfinance` outside prototyping).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

FORBIDDEN = frozenset({"bt", "ffn", "yfinance"})
SRC = Path(__file__).resolve().parents[1] / "src"


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_src_imports_no_forbidden_package() -> None:
    offenders = {
        str(path.relative_to(SRC)): sorted(_imported_roots(ast.parse(path.read_text())) & FORBIDDEN)
        for path in sorted(SRC.rglob("*.py"))
    }
    assert {k: v for k, v in offenders.items() if v} == {}


@pytest.mark.parametrize(
    "source",
    ["import bt", "import yfinance as yf", "from ffn import core", "from bt.core import Strategy"],
)
def test_detector_catches_forbidden_imports(source: str) -> None:
    assert _imported_roots(ast.parse(source)) & FORBIDDEN


def test_detector_ignores_relative_and_lookalike_imports() -> None:
    assert not _imported_roots(ast.parse("from . import bt\nimport btree\n")) & FORBIDDEN

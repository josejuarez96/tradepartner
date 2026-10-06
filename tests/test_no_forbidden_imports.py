"""`bt` is a test-only oracle (ADR 0004): nothing under `src/` imports it.

`ffn` and `yfinance` come in with `bt` as transitive dev dependencies; they
stay out of runtime code too (ADR 0004 avoids `yfinance` outside prototyping).

The research-registry isolation scan (spec req 13) lives here too: the allowlist
for `tradepartner.store.research` and the research tables, no import path between
research and execution, `DatasetRecord.path` read only by `load_dataset`, and no
script importing the research modules.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

FORBIDDEN = frozenset({"bt", "ffn", "yfinance"})
SRC = Path(__file__).resolve().parents[1] / "src"


_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})


def _imported_roots(tree: ast.AST) -> set[str]:
    """Top-level package names imported statically or via a literal dynamic import."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            first = node.args[0]
            if (
                name in _DYNAMIC_IMPORTERS
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
            ):
                roots.add(first.value.split(".")[0])
    return roots


def test_src_imports_no_forbidden_package() -> None:
    offenders = {
        str(path.relative_to(SRC)): sorted(
            _imported_roots(ast.parse(path.read_text(encoding="utf-8"))) & FORBIDDEN
        )
        for path in sorted(SRC.rglob("*.py"))
    }
    assert {k: v for k, v in offenders.items() if v} == {}


@pytest.mark.parametrize(
    "source",
    [
        "import bt",
        "import yfinance as yf",
        "from ffn import core",
        "from bt.core import Strategy",
        "importlib.import_module('bt')",
        "import_module('yfinance.ticker')",
        "__import__('ffn')",
    ],
)
def test_detector_catches_forbidden_imports(source: str) -> None:
    assert _imported_roots(ast.parse(source)) & FORBIDDEN


def test_detector_ignores_relative_and_lookalike_imports() -> None:
    assert not _imported_roots(ast.parse("from . import bt\nimport btree\n")) & FORBIDDEN


# --- Research-registry isolation (research-registry spec req 13, ADR 0008 point 2) ---
#
# The registry records runs; it authorizes nothing. Only the allowlisted modules may
# import `tradepartner.store.research` or name a research table in a string; there is
# no import path between `tradepartner.research` and `tradepartner.execution` either
# way, and none from `tradepartner.research` to an adapter; `DatasetRecord.path` is
# read only by `research.load_dataset`; and no script imports the research modules.

TRADEPARTNER = SRC / "tradepartner"
SCRIPTS = SRC.parent / "scripts"
STORE_RESEARCH = "tradepartner.store.research"
RESEARCH = "tradepartner.research"

#: Req 13's allowlist: a module is allowed when its dotted name is one of these or
#: sits under one of them.
RESEARCH_ALLOWLIST = (
    "tradepartner.research",  # the package itself (handle, reader, parser, gates)
    "tradepartner.store.research",  # the API
    "tradepartner.store.schema",  # the DDL
    "tradepartner.store.registry",  # family_holdout_spends reads research spends
    "tradepartner.backtest.results",  # N reads family_run_count (T83b)
    "tradepartner.cli",  # experiment and dataset commands (T83)
    "tradepartner.dashboard",  # the research view (T83c)
)
_RESEARCH_TABLE = re.compile(r"\bresearch_(registrations|datasets|runs|results|decisions)\b")
#: The registered-file attributes of a `DatasetRecord` only `load_dataset` reads.
_DATASET_PATH_ATTRS = frozenset({"path", "split_path"})


def _module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imported_modules(tree: ast.AST) -> set[str]:
    """Every dotted module a source imports, `from a.b import c` giving both
    `a.b` and `a.b.c` (so `from tradepartner.store import research` counts)."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            first = node.args[0]
            if name in _DYNAMIC_IMPORTERS and isinstance(first, ast.Constant):
                modules.add(str(first.value))
    return modules


def _under(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _imports_any(tree: ast.AST, prefixes: tuple[str, ...]) -> bool:
    return any(_under(m, p) for m in _imported_modules(tree) for p in prefixes)


def _names_research_table(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _RESEARCH_TABLE.search(node.value)
        for node in ast.walk(tree)
    )


def _dataset_path_reads(tree: ast.AST) -> list[str]:
    """`.path`/`.split_path` reads in `tree`, as the enclosing function's name
    (`<module>` at top level). `settings.store.path` (a config value, never a
    registered export) is not one."""
    reads: list[str] = []

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = (
                child.name
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                else function
            )
            if (
                isinstance(child, ast.Attribute)
                and child.attr in _DATASET_PATH_ATTRS
                and not (isinstance(child.value, ast.Attribute) and child.value.attr == "store")
            ):
                reads.append(function)
            visit(child, inner)

    visit(tree, "<module>")
    return reads


def _sources(root: Path) -> list[tuple[str, ast.AST]]:
    return [
        (_module_name(path), ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(root.rglob("*.py"))
    ]


def test_only_the_allowlist_imports_store_research_or_names_a_research_table() -> None:
    offenders = [
        module
        for module, tree in _sources(TRADEPARTNER)
        if not any(_under(module, allowed) for allowed in RESEARCH_ALLOWLIST)
        and (_imports_any(tree, (STORE_RESEARCH,)) or _names_research_table(tree))
    ]
    assert offenders == []


def test_research_imports_no_execution_or_adapter_module() -> None:
    offenders = [
        module
        for module, tree in _sources(TRADEPARTNER / "research")
        if _imports_any(tree, ("tradepartner.execution", "tradepartner.adapters"))
    ]
    assert offenders == []


def test_execution_imports_no_research_module() -> None:
    offenders = [
        module
        for module, tree in _sources(TRADEPARTNER / "execution")
        if _imports_any(tree, (RESEARCH, STORE_RESEARCH))
    ]
    assert offenders == []


def test_dataset_path_is_read_only_in_load_dataset() -> None:
    """Every module that imports a research module reads a `DatasetRecord`'s
    `path` or `split_path` only inside `load_dataset`, and `load_dataset` does
    read it (the scan sees the one reader)."""
    reads = {
        module: _dataset_path_reads(tree)
        for module, tree in _sources(TRADEPARTNER)
        if _under(module, RESEARCH)
        or _under(module, STORE_RESEARCH)
        or _imports_any(tree, (RESEARCH, STORE_RESEARCH))
    }
    assert {m: r for m, r in reads.items() if set(r) - {"load_dataset"}} == {}
    assert "load_dataset" in reads[RESEARCH]


def test_no_script_imports_the_research_modules() -> None:
    offenders = [
        str(path.relative_to(SCRIPTS))
        for path in sorted(SCRIPTS.rglob("*.py"))
        if _imports_any(ast.parse(path.read_text(encoding="utf-8")), (RESEARCH, STORE_RESEARCH))
    ]
    assert offenders == []


@pytest.mark.parametrize(
    "source",
    [
        "import tradepartner.store.research",
        "from tradepartner.store import research",
        "from tradepartner.store.research import open_run",
        "importlib.import_module('tradepartner.store.research')",
    ],
)
def test_the_detector_catches_a_store_research_import(source: str) -> None:
    assert _imports_any(ast.parse(source), (STORE_RESEARCH,))


@pytest.mark.parametrize(
    "source",
    [
        "from tradepartner.research import load_dataset",
        "import tradepartner.research.gates",
        "from tradepartner import research",
    ],
)
def test_the_detector_catches_a_research_import(source: str) -> None:
    assert _imports_any(ast.parse(source), (RESEARCH,))


def test_the_detector_ignores_lookalikes() -> None:
    tree = ast.parse("from tradepartner.store import registry\nimport tradepartner.researcher\n")
    assert not _imports_any(tree, (STORE_RESEARCH, RESEARCH))
    assert _names_research_table(ast.parse("q = 'SELECT 1 FROM research_runs'"))
    assert not _names_research_table(ast.parse("q = 'research_runs_extra'"))


def test_the_path_detector_names_the_enclosing_function() -> None:
    tree = ast.parse(
        "def load_dataset(h):\n    return h.dataset.path\n"
        "def other(h):\n    return h.dataset.split_path, settings.store.path\n"
    )
    assert _dataset_path_reads(tree) == ["load_dataset", "other"]


def test_a_module_outside_the_allowlist_would_fail() -> None:
    """The allowlist is by module: the same import in a module outside it is an
    offender (the scan's own rule, applied to a made-up module name)."""
    tree = ast.parse("from tradepartner.store.research import family_run_count")
    assert not any(_under("tradepartner.gap", allowed) for allowed in RESEARCH_ALLOWLIST)
    assert _imports_any(tree, (STORE_RESEARCH,))
    assert all(
        _under(module, allowed)
        for module, allowed in [
            ("tradepartner.backtest.results", "tradepartner.backtest.results"),
            ("tradepartner.dashboard.research_page", "tradepartner.dashboard"),
        ]
    )

"""The shape of `cli.make_app` (#3648): one pinned command tree, nine group functions.

`make_app` was one function holding every command as a nested closure. It is
split in place, inside `cli.py`, into nine `_register_<group>(app, edges)`
functions; `make_app` builds the app and the edges and calls them in order.

- `test_the_command_tree_is_pinned` is the golden: every command's path and
  parameters, in registration order (which is the help order), compared with
  `tests/fixtures/cli_tree.json`. No help text and no panel name is recorded,
  so rewording a command does not move it. `CLI_TREE_UPDATE=1` rewrites it.
- `test_make_app_only_delegates` and `test_each_group_reads_only_its_edges`
  read `cli.py`'s syntax tree and hold the split's shape.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Any

import typer.main

from tradepartner import cli

ROOT = Path(__file__).resolve().parents[1]
CLI_SOURCE = ROOT / "src" / "tradepartner" / "cli.py"
GOLDEN = Path(__file__).parent / "fixtures" / "cli_tree.json"
UPDATE_VARIABLE = "CLI_TREE_UPDATE"

#: Each group function and the edges it reads off `edges`, in the order
#: `make_app` calls them (the order the commands were registered in before the split).
GROUP_EDGES: dict[str, frozenset[str]] = {
    "_register_data": frozenset(
        {"settings", "clock", "edgar_client", "price_source", "rate_source", "assets_source"}
    ),
    "_register_reports": frozenset({"settings", "clock", "launcher"}),
    "_register_backtest": frozenset({"settings"}),
    "_register_decisions": frozenset({"settings"}),
    "_register_lab": frozenset({"settings", "clock", "sweep_clock"}),
    "_register_store": frozenset({"settings", "clock", "icloud"}),
    "_register_research": frozenset(
        {"settings", "clock", "edgar_client", "launcher", "model_client"}
    ),
    "_register_paper": frozenset({"settings", "clock", "broker", "parse_export"}),
    "_register_checks": frozenset({"settings", "clock"}),
}


def _parameter(param: Any) -> dict[str, Any]:
    """One Click parameter's record: its names and arity, never its help text."""
    record: dict[str, Any] = {
        "name": param.name,
        "opts": list(param.opts),
        "secondary_opts": list(param.secondary_opts),
        "required": param.required,
        "nargs": param.nargs,
        "type": param.type.name,
    }
    if param.param_type_name == "option":
        record["is_flag"] = param.is_flag
    return record


def _node(command: Any, path: tuple[str, ...]) -> dict[str, Any]:
    """`command`'s record and, in registration order, its sub-commands' records."""
    children = getattr(command, "commands", {})
    return {
        "path": " ".join(path),
        "params": [_parameter(p) for p in command.params],
        "commands": [_node(child, (*path, name)) for name, child in children.items()],
    }


def _tree() -> str:
    """The whole command tree as sorted-key JSON (lists keep their order)."""
    root = typer.main.get_command(cli.make_app())
    return json.dumps(_node(root, ()), indent=2, sort_keys=True) + "\n"


def _functions() -> dict[str, ast.FunctionDef]:
    """`cli.py`'s module-level functions by name."""
    tree = ast.parse(CLI_SOURCE.read_text(encoding="utf-8"))
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def test_the_command_tree_is_pinned() -> None:
    tree = _tree()
    if os.environ.get(UPDATE_VARIABLE) == "1":
        GOLDEN.write_text(tree, encoding="utf-8")
    assert tree == GOLDEN.read_text(encoding="utf-8"), (
        f"the command tree differs from {GOLDEN.relative_to(ROOT)}: a command, option or "
        f"argument was added, removed, renamed or reordered. If that is intended, rerun "
        f"with {UPDATE_VARIABLE}=1 to rewrite the file and commit it."
    )


def test_make_app_only_delegates() -> None:
    make_app = _functions()["make_app"]
    nested = [
        node.name
        for node in ast.walk(make_app)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not make_app
    ]
    assert nested == [], f"make_app holds {len(nested)} nested functions"
    registered = [
        node.func.id
        for node in ast.walk(make_app)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.startswith("_register_")
    ]
    assert registered == list(GROUP_EDGES)


def test_each_group_reads_only_its_edges() -> None:
    functions = _functions()
    read = {
        name: frozenset(
            node.attr
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "edges"
        )
        for name in GROUP_EDGES
        if name in functions
    }
    assert read == GROUP_EDGES

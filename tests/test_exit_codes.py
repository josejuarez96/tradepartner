"""The exit codes are pinned, and `cli.py` passes none as a bare integer (#3651)."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import typer
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.execution import wrapper
from tradepartner.exit_codes import ExitCode, LotsReconcileExit

CLI_SOURCE = Path(cli.__file__)


def _leaves(node: ast.expr) -> Iterator[ast.expr]:
    """`node`, or each branch of a conditional expression (`a if x else b`)."""
    if isinstance(node, ast.IfExp):
        yield from _leaves(node.body)
        yield from _leaves(node.orelse)
    else:
        yield node


def _exit_code_argument(call: ast.Call) -> ast.expr | None:
    """The exit-code argument of a `typer.Exit(...)` or `_fail(...)` call, else None."""
    func = call.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "Exit"
        and isinstance(func.value, ast.Name)
        and func.value.id == "typer"
    ):
        position = 0
    elif isinstance(func, ast.Name) and func.id == "_fail":
        position = 1
    else:
        return None
    for keyword in call.keywords:
        if keyword.arg == "code":
            return keyword.value
    return call.args[position] if len(call.args) > position else None


def test_cli_passes_no_bare_integer_as_an_exit_code() -> None:
    tree = ast.parse(CLI_SOURCE.read_text(encoding="utf-8"))
    calls = 0
    literals: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        argument = _exit_code_argument(node)
        if argument is None:
            continue
        calls += 1
        literals.extend(
            f"cli.py:{leaf.lineno}: {leaf.value}"
            for leaf in _leaves(argument)
            if isinstance(leaf, ast.Constant) and isinstance(leaf.value, int)
        )
    assert calls > 100  # the walk found the call sites; an empty walk proves nothing
    assert literals == [], f"{len(literals)} bare integer exit codes"


def test_the_exit_codes_are_pinned() -> None:
    assert {m.name: m.value for m in ExitCode} == {
        "OK": 0,
        "FAILED": 1,
        "USAGE": 2,
        "WRITE_FAILED": 3,
        "REFUSED": 4,
        "LOCKED": 5,
        "NO_WINDOW": 6,
        "INTERRUPTED": 130,
    }
    assert {m.name: m.value for m in LotsReconcileExit} == {
        "DIFFERENCE": 3,
        "UNKNOWN_EXPORT_FORMAT": 4,
        "NO_DISPOSALS": 5,
        "LOCKED": 6,
        "NO_WINDOW": 7,
        "NO_STORE": 8,
        "SCHEMA_VERSION": 9,
    }


def test_the_old_names_keep_their_values() -> None:
    assert cli.USAGE_ERROR == 2
    assert cli.INTERRUPTED_EXIT == 130
    assert dict(cli.PAPER_REFUSAL_EXIT) == {"refused": 4, "locked": 5, "no_window": 6}
    assert cli.LOTS_RECONCILE_DIFFERENCE_EXIT == 3
    assert dict(cli.LOTS_RECONCILE_REFUSAL_EXIT) == {
        "unknown_export_format": 4,
        "no_disposals": 5,
        "locked": 6,
        "no_window": 7,
        "no_store": 8,
        "schema_version": 9,
    }
    assert cli.STATUS_EXIT == {
        "ok": 0,
        "failed": 1,
        "refused_window": 2,
        "refused_holdout": 2,
        "refused_gap": 2,
        "refused_variant": 2,
    }
    assert wrapper.CRASH_EXIT_CODE == ExitCode.FAILED
    assert wrapper.WRITE_FAILED_EXIT_CODE == ExitCode.WRITE_FAILED


def test_a_member_leaves_the_process_as_its_integer() -> None:
    app = typer.Typer()

    @app.command()
    def refuse() -> None:
        raise typer.Exit(ExitCode.REFUSED)

    result = CliRunner().invoke(app, [])
    assert result.exit_code == 4
    assert f"exited {ExitCode.WRITE_FAILED}" == "exited 3"

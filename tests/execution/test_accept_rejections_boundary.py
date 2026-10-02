"""`--accept-rejections` is the owner's flag and nothing else's (#472).

Static checks over `src/tradepartner/` and `scripts/`, in the style of
`test_boundaries.py`:

1. The name `accept_rejections` (or `accept-rejections`), as an identifier, an
   attribute, a keyword, a parameter or inside a string, appears only in
   `ALLOWED`: `execution/resume.py`, which takes it, and the store modules that
   journal it. No run, scheduler, script or config module names it.
2. Nothing gives it a default: no parameter or dataclass field named
   `accept_rejections` has one, so a caller that forgets it fails rather than
   quietly passing a value.
3. Every `accept_rejections=` keyword passes the caller's own
   `accept_rejections` name on, never a literal or another expression.
4. No module imports `execution.resume` but `RESUME_CALLERS` (none yet: the
   `paper resume` CLI is T67, which adds `cli.py` here and to `ALLOWED`, with
   the flag as an explicit `argparse` `store_true` option).
5. No config field anywhere in `Settings` is named for it.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from pydantic import BaseModel

from tradepartner.config import Settings
from tradepartner.execution.resume import resume
from tradepartner.store.journal import ResumeInvocationRow

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
FLAG = "accept_rejections"
SPELLINGS = (FLAG, "accept-rejections")
RESUME_MODULE = "tradepartner.execution.resume"
ALLOWED = frozenset(
    {
        RESUME_MODULE,  # takes the flag, journals it, applies it
        "tradepartner.store.journal",  # ResumeInvocationRow.accept_rejections
        "tradepartner.store.schema",  # the column's DDL and its migration
    }
)
RESUME_CALLERS: frozenset[str] = frozenset()


def _modules() -> dict[str, ast.Module]:
    found: dict[str, ast.Module] = {}
    for path in sorted([*SRC.rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]):
        if path.is_relative_to(SRC):
            parts = list(path.relative_to(SRC).with_suffix("").parts)
        else:
            parts = ["scripts", *path.relative_to(ROOT / "scripts").with_suffix("").parts]
        if parts[-1] == "__init__":
            parts.pop()
        found[".".join(parts)] = ast.parse(path.read_text(encoding="utf-8"))
    return found


MODULES = _modules()


def _mentions(tree: ast.Module) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        named = (
            (isinstance(node, ast.Name) and node.id == FLAG)
            or (isinstance(node, ast.Attribute) and node.attr == FLAG)
            or (isinstance(node, ast.keyword) and node.arg == FLAG)
            or (isinstance(node, ast.arg) and node.arg == FLAG)
            or (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and any(s in node.value for s in SPELLINGS)
            )
        )
        if named:
            lines.append(getattr(node, "lineno", 0))
    return lines


def test_the_scan_sees_the_modules_that_take_the_flag() -> None:
    assert set(MODULES) >= ALLOWED
    assert all(_mentions(MODULES[name]) for name in ALLOWED)


def test_only_the_resume_and_its_journal_name_the_flag() -> None:
    offenders = {
        name: lines
        for name, tree in MODULES.items()
        if name not in ALLOWED and (lines := _mentions(tree))
    }
    assert offenders == {}


def test_nothing_gives_the_flag_a_default() -> None:
    defaulted = []
    for name, tree in MODULES.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.arguments):
                positional = [*node.posonlyargs, *node.args]
                with_defaults = positional[len(positional) - len(node.defaults) :]
                defaulted += [f"{name}:{a.lineno}" for a in with_defaults if a.arg == FLAG]
                defaulted += [
                    f"{name}:{a.lineno}"
                    for a, d in zip(node.kwonlyargs, node.kw_defaults, strict=True)
                    if a.arg == FLAG and d is not None
                ]
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == FLAG
                and node.value is not None
            ):
                defaulted.append(f"{name}:{node.lineno}")
    assert defaulted == []
    parameter = inspect.signature(resume).parameters[FLAG]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    field = ResumeInvocationRow.__dataclass_fields__[FLAG]
    assert field.default is field.default_factory  # both MISSING: no default


def test_every_keyword_passes_the_callers_own_flag_on() -> None:
    passed = []
    for name, tree in MODULES.items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.keyword)
                and node.arg == FLAG
                and not (isinstance(node.value, ast.Name) and node.value.id == FLAG)
            ):
                passed.append(f"{name}:{node.value.lineno}: {ast.unparse(node.value)}")
    assert passed == []


def test_only_the_cli_may_import_resume() -> None:
    importers = set()
    for name, tree in MODULES.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                targets = [base, *(f"{base}.{alias.name}" for alias in node.names)]
            else:
                continue
            if any(t == RESUME_MODULE or t.startswith(RESUME_MODULE + ".") for t in targets):
                importers.add(name)
            # A relative import (`from . import resume`) inside execution/.
            if (
                isinstance(node, ast.ImportFrom)
                and node.level
                and any(alias.name == "resume" for alias in node.names)
            ):
                importers.add(name)
    assert importers - {RESUME_MODULE} <= RESUME_CALLERS


def _field_names(model: type[BaseModel]) -> set[str]:
    names = set()
    for field_name, field in model.model_fields.items():
        names.add(field_name)
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            names |= _field_names(annotation)
    return names


def test_no_config_key_reaches_the_flag() -> None:
    names = _field_names(Settings)
    assert names  # the walk sees the config
    assert not any("reject" in n and "accept" in n for n in names)

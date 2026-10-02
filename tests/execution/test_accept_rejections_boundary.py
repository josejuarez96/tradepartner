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
   `accept_rejections` name on, never a literal or another expression. The name
   is never bound except as a parameter or a dataclass field (no
   `accept_rejections = True`), never passed positionally (but to `isinstance`
   or `type`, for `resume`'s own type check), and never spelt as
   a whole string (no `**{"accept_rejections": True}`).
4. No module imports `execution.resume` except those in `RESUME_CALLERS`. There
   are none yet: the `paper resume` CLI is T67. T67 adds `cli.py` here and to
   `ALLOWED`, with the flag as an explicit `argparse` `store_true` option and one
   reviewed exception to rule 3 for `accept_rejections=args.accept_rejections`.
5. No config field anywhere in `Settings` is named for it.

`test_each_rule_refuses_a_sample_that_breaks_it` runs each rule on a sample
source that breaks it, so a rule that silently stops matching fails too.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable
from pathlib import Path

import pytest
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
#: Builtins the flag may be passed to positionally: `resume`'s own type check.
_TYPE_CHECKS = frozenset({"isinstance", "type"})


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


def mentions(tree: ast.Module) -> list[int]:
    """Rule 1: every line naming the flag."""
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


def defaults(tree: ast.Module) -> list[int]:
    """Rule 2: a parameter or a dataclass field named for the flag with a default."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arguments):
            positional = [*node.posonlyargs, *node.args]
            with_defaults = positional[len(positional) - len(node.defaults) :]
            lines += [a.lineno for a in with_defaults if a.arg == FLAG]
            lines += [
                a.lineno
                for a, d in zip(node.kwonlyargs, node.kw_defaults, strict=True)
                if a.arg == FLAG and d is not None
            ]
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == FLAG
            and node.value is not None
        ):
            lines.append(node.lineno)
    return lines


def misuses(tree: ast.Module) -> list[str]:
    """Rule 3: a keyword passing anything but the caller's own flag, a binding
    other than a parameter or a field, a positional pass, or a whole-string key."""
    fields = {
        id(node.target)
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign) and node.value is None
    }
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.keyword)
            and node.arg == FLAG
            and not (isinstance(node.value, ast.Name) and node.value.id == FLAG)
        ):
            found.append(f"{node.value.lineno}: passes {ast.unparse(node.value)}")
        elif (
            isinstance(node, ast.Name)
            and node.id == FLAG
            and isinstance(node.ctx, ast.Store)
            and id(node) not in fields
        ):
            found.append(f"{node.lineno}: binds the name")
        elif isinstance(node, ast.Call) and not (
            isinstance(node.func, ast.Name) and node.func.id in _TYPE_CHECKS
        ):
            for arg in node.args:
                inner = arg.value if isinstance(arg, ast.Starred) else arg
                if isinstance(inner, ast.Name) and inner.id == FLAG:
                    found.append(f"{arg.lineno}: passed positionally")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.strip() in SPELLINGS
        ):
            found.append(f"{node.lineno}: spelt as a whole string")
    return found


def imports_resume(tree: ast.Module) -> bool:
    """Rule 4: whether the module imports `execution.resume`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            targets = [base, *(f"{base}.{alias.name}" for alias in node.names)]
            # A relative import (`from . import resume`) inside execution/.
            if node.level and any(alias.name == "resume" for alias in node.names):
                return True
        else:
            continue
        if any(t == RESUME_MODULE or t.startswith(RESUME_MODULE + ".") for t in targets):
            return True
    return False


def test_the_scan_sees_the_modules_that_take_the_flag() -> None:
    assert set(MODULES) >= ALLOWED
    assert all(mentions(MODULES[name]) for name in ALLOWED)


def test_only_the_resume_and_its_journal_name_the_flag() -> None:
    offenders = {
        name: lines
        for name, tree in MODULES.items()
        if name not in ALLOWED and (lines := mentions(tree))
    }
    assert offenders == {}


def test_nothing_gives_the_flag_a_default() -> None:
    assert {name: lines for name, tree in MODULES.items() if (lines := defaults(tree))} == {}
    parameter = inspect.signature(resume).parameters[FLAG]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    field = ResumeInvocationRow.__dataclass_fields__[FLAG]
    assert field.default is field.default_factory  # both MISSING: no default


def test_the_flag_is_only_ever_passed_on_by_its_own_name() -> None:
    assert {name: found for name, tree in MODULES.items() if (found := misuses(tree))} == {}


def test_only_the_cli_may_import_resume() -> None:
    importers = {name for name, tree in MODULES.items() if imports_resume(tree)}
    assert importers - {RESUME_MODULE} <= RESUME_CALLERS


Rule = Callable[[ast.Module], object]


@pytest.mark.parametrize(
    ("source", "rule"),
    [
        ("resume(s, c, b, k, 'r', False, accept_rejections=True)", misuses),
        ("resume(s, c, b, k, 'r', False, accept_rejections=args.flag)", misuses),
        ("accept_rejections = True", misuses),
        ("for accept_rejections in (True,): pass", misuses),
        ("_start(c, w, n, r, f, accept_rejections)", misuses),
        ("resume(**{'accept_rejections': True})", misuses),
        ("def resume(*, accept_rejections: bool = False): ...", defaults),
        ("def resume(accept_rejections: bool = False): ...", defaults),
        ("class Row:\n    accept_rejections: bool = False", defaults),
        ("PAPER = {'paper.accept_rejections': True}", mentions),
        ("from tradepartner.execution.resume import resume", imports_resume),
        ("from tradepartner.execution import resume", imports_resume),
    ],
)
def test_each_rule_refuses_a_sample_that_breaks_it(source: str, rule: Rule) -> None:
    assert rule(ast.parse(source)), source


def test_the_rules_let_the_callers_own_flag_through() -> None:
    allowed = ast.parse(
        "def resume(*, accept_rejections: bool):\n"
        "    _start(c, accept_rejections=accept_rejections)\n"
        "class Row:\n"
        "    accept_rejections: bool\n"
    )
    assert misuses(allowed) == []
    assert defaults(allowed) == []


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

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
3. The check fails closed: every `Load` of the bare name must sit in one of
   four safe contexts, or it is reported. The safe contexts are the value of
   an `accept_rejections=` keyword to a callee other than `dict` (passing the
   caller's own flag on; `dict(accept_rejections=accept_rejections)` would
   launder it into an iterable), the sole, unkeyworded, by-identity first
   argument of a 1-arg `type` call or the sole, unkeyworded, by-identity
   first-of-2 argument of a 2-arg `isinstance(x, bool)` call, its second
   argument the bare name `bool` (`resume`'s own type check; a module that
   rebinds `isinstance`, `type` or `bool`, or uses a star import that
   could, is refused outright - this makes the calls the builtins in every
   module this fence can see, not in every module there is), the whole
   `test` of an `if`, or
   (for a `Store`) the target of a bare annotated field. Everything else -
   a literal or other expression under the `accept_rejections=` keyword,
   the name under any other keyword, a positional pass, smuggling through
   a starred list/tuple/set/dict display, a multiplied or generator
   expression, an `IfExp`, an alias bound first and starred later, `del`,
   or a PEP 695 type parameter named for it - is refused. The name is never
   otherwise bound (no `accept_rejections = True`, no match, except, import,
   global or nonlocal capture, and no `def`, `async def` or `class` named
   for it), and every such parameter is keyword-only (so no positional
   argument can feed it).
4. No module imports `execution.resume` except those in `RESUME_CALLERS`. There
   are none yet: the `paper resume` CLI is T67. T67 adds `cli.py` here and to
   `ALLOWED`, with the flag as an explicit `argparse` `store_true` option and one
   reviewed exception to rule 3 for `accept_rejections=args.accept_rejections`.
5. No config field anywhere in `Settings` is named for it.

Known limits (#696), not checked here: a value laundered through control
flow into a new name (`if accept_rejections: f = True`) breaks the name-based
premise entirely; a positional `ast.Attribute` load (`row.accept_rejections`)
inside an `ALLOWED` module is not checked the way a bare `Name` is; and the
`isinstance`, `type` or `bool` exemption still trusts the interpreter's own
builtins and import machinery - a `**kwargs` collector other than `dict` by
name (an alias of `dict`, `builtins.dict`, `collections.OrderedDict`,
`SimpleNamespace`, or any plain function taking `**kwargs`), a class keyword
(`accept_rejections` passed as `metaclass=...` or similar), patching
`builtins.isinstance` or `builtins.bool` itself, or reaching any of these
names through `globals()[...]` all sit outside what a static AST scan can
see.

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
#: Names the fence refuses to see rebound anywhere: `resume`'s own type
#: check (`isinstance`, `type`) and the type `isinstance` is always checked
#: against here (`bool`), so a custom `__instancecheck__` can't see the flag.
_TYPE_CHECKS = frozenset({"isinstance", "type", "bool"})


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
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name == FLAG
            )
            or (
                isinstance(node, (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple))
                and node.name == FLAG
            )
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


def _parent_map(tree: ast.Module) -> dict[int, ast.AST]:
    """Maps each node's `id()` to its parent, so a `Load` of the flag can be
    classified by where it sits without re-deriving an unwrapper for every
    new smuggling shape (a display, a generator, an `IfExp`, ...)."""
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _is_type_check(call: ast.Call, node: ast.expr) -> bool:
    """Whether `call` is exactly `resume`'s own type check with `node` as the
    argument being checked: `type(x)` (1 positional arg, no keywords, `node`
    is it) or `isinstance(x, bool)` (2 positional args, no keywords, `node`
    is the first by identity, never by position alone, and the second is the
    bare name `bool` - never a variable, so a custom `__instancecheck__`
    cannot see the value)."""
    if call.keywords or not isinstance(call.func, ast.Name):
        return False
    if call.func.id == "type":
        return len(call.args) == 1 and call.args[0] is node
    if call.func.id == "isinstance":
        return (
            len(call.args) == 2
            and call.args[0] is node
            and isinstance(call.args[1], ast.Name)
            and call.args[1].id == "bool"
        )
    return False


def misuses(tree: ast.Module) -> list[str]:
    """Rule 3, fail closed: a parameter that is not keyword-only (so no
    positional argument can feed it), a binding other than a keyword-only
    parameter or a bare annotated field, a PEP 695 type parameter named for
    it, a whole-string spelling, a keyword passing anything but the caller's
    own flag under `accept_rejections=` to a callee other than `dict`, any
    rebinding of `isinstance`, `type` or `bool` (the type-check exemption
    below only holds if those names are still the builtins), or (the
    catch-all) any
    `Load` of the bare name that is not the value of such a keyword, the
    checked argument of `resume`'s own `isinstance`/`type` call, or the whole
    `test` of an `if`. Anything else - smuggled through a display, a
    multiplied or generator expression, an `IfExp`, an alias bound first,
    another keyword, `del`, or laundered through `dict(...)` - is reported
    rather than special-cased."""
    fields = {
        id(node.target)
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign) and node.value is None
    }
    parents = _parent_map(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arguments):
            loose = [*node.posonlyargs, *node.args, node.vararg, node.kwarg]
            found += [
                f"{a.lineno}: not keyword-only" for a in loose if a is not None and a.arg == FLAG
            ]
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == FLAG
        ):
            found.append(f"{node.lineno}: binds the name in a def/class")
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in _TYPE_CHECKS
        ):
            found.append(f"{node.lineno}: rebinds {node.name} in a def/class")
        elif isinstance(node, (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple)) and node.name == FLAG:
            found.append(f"{node.lineno}: binds the name in a type parameter")
        elif (
            isinstance(node, (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple))
            and node.name in _TYPE_CHECKS
        ):
            found.append(f"{node.lineno}: rebinds {node.name} in a type parameter")
        elif (isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == FLAG) or (
            isinstance(node, ast.MatchMapping) and node.rest == FLAG
        ):
            found.append(f"{node.lineno}: binds the name in a match")
        elif (isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name in _TYPE_CHECKS) or (
            isinstance(node, ast.MatchMapping) and node.rest in _TYPE_CHECKS
        ):
            found.append(f"{node.lineno}: rebinds isinstance/type in a match")
        elif isinstance(node, ast.ExceptHandler) and node.name == FLAG:
            found.append(f"{node.lineno}: binds the name in an except")
        elif isinstance(node, ast.ExceptHandler) and node.name in _TYPE_CHECKS:
            found.append(f"{node.lineno}: rebinds {node.name} in an except")
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and any(
            (alias.asname or alias.name) == FLAG for alias in node.names
        ):
            found.append(f"{node.lineno}: binds the name in an import")
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and any(
            (alias.asname or alias.name) in _TYPE_CHECKS for alias in node.names
        ):
            found.append(f"{node.lineno}: rebinds isinstance/type in an import")
        elif isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
            found.append(f"{node.lineno}: a star import can rebind isinstance/type unseen")
        elif isinstance(node, ast.arg) and node.arg in _TYPE_CHECKS:
            found.append(f"{node.lineno}: rebinds {node.arg} as a parameter")
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and FLAG in node.names:
            found.append(f"{node.lineno}: declares the name {type(node).__name__.lower()}")
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and any(
            n in _TYPE_CHECKS for n in node.names
        ):
            found.append(f"{node.lineno}: rebinds isinstance/type {type(node).__name__.lower()}")
        elif (
            isinstance(node, ast.keyword)
            and node.arg == FLAG
            and not (isinstance(node.value, ast.Name) and node.value.id == FLAG)
        ):
            found.append(f"{node.value.lineno}: passes {ast.unparse(node.value)}")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.strip() in SPELLINGS
        ):
            found.append(f"{node.lineno}: spelt as a whole string")
        elif (
            isinstance(node, ast.Name)
            and node.id in _TYPE_CHECKS
            and isinstance(node.ctx, ast.Store)
        ):
            found.append(f"{node.lineno}: rebinds {node.id}")
        elif isinstance(node, ast.Name) and node.id == FLAG:
            if isinstance(node.ctx, ast.Store):
                if id(node) not in fields:
                    found.append(f"{node.lineno}: binds the name")
            elif isinstance(node.ctx, ast.Load):
                parent = parents.get(id(node))
                keyword_callee = (
                    parents.get(id(parent)) if isinstance(parent, ast.keyword) else None
                )
                safe = (
                    (
                        isinstance(parent, ast.keyword)
                        and parent.arg == FLAG
                        and parent.value is node
                        and not (
                            isinstance(keyword_callee, ast.Call)
                            and isinstance(keyword_callee.func, ast.Name)
                            and keyword_callee.func.id == "dict"
                        )
                    )
                    or (isinstance(parent, ast.Call) and _is_type_check(parent, node))
                    or (isinstance(parent, ast.If) and parent.test is node)
                )
                if not safe:
                    where = ast.unparse(parent) if parent is not None else ast.unparse(node)
                    found.append(f"{node.lineno}: unclassified use: {where}")
            else:
                found.append(f"{node.lineno}: deletes the name")
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
        ("def _start(c, accept_rejections: bool): ...", misuses),
        ("def _start(accept_rejections, /): ...", misuses),
        ("def _start(*accept_rejections): ...", misuses),
        ("def _start(**accept_rejections): ...", misuses),
        ("match x:\n    case accept_rejections: pass", misuses),
        ("match x:\n    case [*accept_rejections]: pass", misuses),
        ("match x:\n    case {**accept_rejections}: pass", misuses),
        ("try: pass\nexcept E as accept_rejections: pass", misuses),
        ("from flags import x as accept_rejections", misuses),
        ("def f():\n    global accept_rejections", misuses),
        ("def f():\n    def g():\n        nonlocal accept_rejections", misuses),
        ("def accept_rejections(): ...", misuses),
        ("async def accept_rejections(): ...", misuses),
        ("class accept_rejections: ...", misuses),
        ("_start(c, *[accept_rejections])", misuses),
        ("_start(c, *(accept_rejections,))", misuses),
        ("_start(c, *{accept_rejections: 0})", misuses),
        ("_start(c, **{accept_rejections: 0})", misuses),
        ("_start(c, *{accept_rejections})", misuses),
        ("_start(c, *([accept_rejections] * 1))", misuses),
        ('_start(c, *(accept_rejections for _ in "x"))', misuses),
        ("_start(c, accept_rejections if x else y)", misuses),
        ("_start(c, *([accept_rejections] if x else []))", misuses),
        ("x = [accept_rejections]\n_start(*x)", misuses),
        ("_start(c, other=accept_rejections)", misuses),
        ("del accept_rejections", misuses),
        ("def f[accept_rejections](): ...", misuses),
        ("def f[accept_rejections](): ...", mentions),
        ("class C[**accept_rejections]: ...", misuses),
        ("class C[**accept_rejections]: ...", mentions),
        ("type A[*accept_rejections] = int", misuses),
        ("type A[*accept_rejections] = int", mentions),
        ("isinstance(x, accept_rejections)", misuses),
        ("type('X', (), accept_rejections)", misuses),
        ("isinstance(accept_rejections, bool, extra=1)", misuses),
        ("from x import _start as isinstance\nisinstance(accept_rejections, c)", misuses),
        ("_start(c, *dict(accept_rejections=accept_rejections).values())", misuses),
        ("from m import *\nisinstance(accept_rejections, bool)", misuses),
        ("isinstance(accept_rejections, Spy)", misuses),
        ("bool = Spy\nisinstance(accept_rejections, bool)", misuses),
        ("from m import Spy as bool\nisinstance(accept_rejections, bool)", misuses),
        ("def accept_rejections(): ...", mentions),
        ("async def accept_rejections(): ...", mentions),
        ("class accept_rejections: ...", mentions),
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
        "    if not isinstance(accept_rejections, bool):\n"
        "        raise TypeError(f'{type(accept_rejections).__name__}')\n"
        "    _start(c, accept_rejections=accept_rejections)\n"
        "    if accept_rejections:\n"
        "        pass\n"
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

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
   an `accept_rejections=` keyword to a reviewed callee in
   `_KEYWORD_CALLEES` (`resume`'s own `_start` and the journal's
   `ResumeInvocationRow`, passing the caller's own flag on; any other
   callee, such as `dict`, an alias of it, `OrderedDict`, `SimpleNamespace`,
   a local `def f(**kw)` or a class keyword, could collect it into a
   mapping and feed it on positionally), the sole, unkeyworded,
   by-identity first argument of a 1-arg `type` call or the sole,
   unkeyworded, by-identity first-of-2 argument of a 2-arg
   `isinstance(x, bool)` call, its second argument the bare name `bool`
   (`resume`'s own type check), the whole `test` of an `if`, or (for a
   `Store`) the target of a bare annotated field. The exemptions hold only
   while the names they trust are what they say, so the fence refuses
   outright: any rebinding of `isinstance`, `type` or `bool`, a star import
   (which could rebind them unseen), any attribute store to one of those
   names, or to any attribute or item reached from one of them or from a
   reviewed callee, however deep (`builtins.isinstance = f`,
   `ResumeInvocationRow.__init__.__code__ = g`), any use or import of
   `builtins`, any use of `__builtins__`, `__import__`, `globals`,
   `locals` or a bare `vars()`, any `__builtins__` or `__globals__`
   attribute, a `__dict__` attribute other than as a `**` unpacking source,
   and, in a module that passes the keyword, any binding of a reviewed
   callee but its own undecorated `def _start(..., *, accept_rejections)`
   with no `**kwargs` or
   `from tradepartner.store.journal import ResumeInvocationRow`. Any
   attribute named for the flag (`row.accept_rejections`) is refused in any
   position. Everything else - a literal or other expression under the
   `accept_rejections=` keyword, the name under any other keyword, a
   positional pass, smuggling through a starred list/tuple/set/dict
   display, a multiplied or generator expression, an `IfExp`, an alias
   bound first and starred later, `del`, or a PEP 695 type parameter named
   for it - is refused. The name is never otherwise bound (no
   `accept_rejections = True`, no match, except, import, global or nonlocal
   capture, and no `def`, `async def` or `class` named for it), and every
   such parameter is keyword-only (so no positional argument can feed it).
4. No module imports `execution.resume` except those in `RESUME_CALLERS`: only
   `cli.py`, the `paper resume` command (T67). `cli.py` is in `ALLOWED` and
   `resume` in `_KEYWORD_CALLEES`, and rule 3 has exactly one reviewed exception
   for it (`_is_cli_pass`): the Typer option `--accept-rejections` is the
   parameter `accept_rejections_flag` (`CLI_FLAG`, a different name, so its
   `False` default is the `store_true` default and rule 2 still holds for every
   `accept_rejections` parameter), bound once, as a parameter whose default is
   the constant `False`, and loaded only as the value of
   `paper_resume.resume(..., accept_rejections=accept_rejections_flag)`. Its
   value can come only from the owner's command line: the parameter belongs to
   the function decorated `@<app>.command("resume")`, whose name is never
   loaded (no direct call, alias or `partial`), its `Annotated` option is
   exactly `typer.Option("--accept-rejections", help=...,
   allow_from_autoenv=False)` (no envvar, auto-envvar, callback, default or
   flag value), the decorator takes no keyword (no `context_settings`), and
   no default map can reach it (#1309): `context_settings` goes only to a
   `.command(...)` decorator, `Typer` is only called or annotated, `**` goes
   only into the ledger row types, nothing names `setattr`, `getattr`,
   `get_command`, `make_context`, `.main`, `.info` or `.context_settings`,
   and `default_map` appears nowhere (`_app_default_misuses`), no
   keyword names `accept_rejections_flag`,
   and no string in `cli.py` spells the flag except docstrings and that one
   option name (so the app cannot invoke itself with it). A second such pass, a
   literal or any other expression under the keyword, any other use or binding
   of `accept_rejections_flag`, or the same shape in any other module, is
   refused.
5. No config field anywhere in `Settings` is named for it.

Known limits, not checked here (#696 closed what a name-based scan can):
a value laundered through control flow into a new name (`if
accept_rejections: f = True`) breaks the name-based premise entirely, and
`resume`'s own `if accept_rejections:` body is exactly such a laundering,
by design; a reviewed callee is trusted to do what its review says with the
flag; and the interpreter's machinery can still be reached in ways no static
scan sees: `sys.modules[...]`, `importlib`, `vars(module)`, `setattr` or
`delattr` on a module or callee obtained some other way, frame objects
(`sys._getframe().f_globals`), `exec`/`eval` of a built string, or
`getattr` with a computed name (the journal's own row writer reads every
field this way, including the flag, to store it).

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
CLI_MODULE = "tradepartner.cli"
ALLOWED = frozenset(
    {
        RESUME_MODULE,  # takes the flag, journals it, applies it
        CLI_MODULE,  # `paper resume --accept-rejections` (T67), rule 4's one exception
        "tradepartner.store.journal",  # ResumeInvocationRow.accept_rejections
        "tradepartner.store.schema",  # the column's DDL and its migration
    }
)
RESUME_CALLERS: frozenset[str] = frozenset({CLI_MODULE})
#: The `paper resume` Typer parameter behind `--accept-rejections` (rule 4).
CLI_FLAG = "accept_rejections_flag"
#: Names the fence refuses to see rebound anywhere: `resume`'s own type
#: check (`isinstance`, `type`) and the type `isinstance` is always checked
#: against here (`bool`), so a custom `__instancecheck__` can't see the flag.
_TYPE_CHECKS = frozenset({"isinstance", "type", "bool"})
#: Names that reach the builtins or a module's own namespace, through which
#: `isinstance`, `type` or `bool` could be patched without a binding the
#: fence sees (`builtins.isinstance = f`, `globals()["bool"] = Spy`).
_REACH = frozenset({"builtins", "__builtins__", "__import__", "globals", "locals"})
#: Attributes that reach a module's namespace or the builtins' from any
#: object (`os.__builtins__`, `_start.__globals__`); `__dict__` is allowed
#: only as the source of a `**` unpacking (`{**row.__dict__}`), a read.
_REACH_ATTRIBUTES = frozenset({"__builtins__", "__globals__", "__dict__"})
#: The only callees the flag may be passed on to under `accept_rejections=`:
#: `resume`'s own `_start` and the journal row. Any other callee (`dict`, an
#: alias of it, `OrderedDict`, `SimpleNamespace`, a local `def f(**kw)`) could
#: collect it into a mapping and feed it on positionally; and `resume`, which
#: `resume.py` defines and `cli.py` reaches only through rule 4's one exception.
_KEYWORD_CALLEES = frozenset({"_start", "resume", "ResumeInvocationRow"})
JOURNAL_MODULE = "tradepartner.store.journal"


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


def _bound(node: ast.AST) -> list[tuple[str, str]]:
    """Every name `node` binds, with how: the one list each rebinding check
    reads, so a new binding form is added once rather than per pinned name."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [(node.name, "a def/class")]
    if isinstance(node, (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple)):
        return [(node.name, "a type parameter")]
    if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name is not None:
        return [(node.name, "a match")]
    if isinstance(node, ast.MatchMapping) and node.rest is not None:
        return [(node.rest, "a match")]
    if isinstance(node, ast.ExceptHandler) and node.name is not None:
        return [(node.name, "an except")]
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return [((a.asname or a.name).split(".")[0], "an import") for a in node.names]
    if isinstance(node, ast.arg):
        return [(node.arg, "a parameter")]
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return [(n, f"a {type(node).__name__.lower()} declaration") for n in node.names]
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        return [(node.id, "an assignment or del")]
    return []


def _is_reviewed_callee_binding(node: ast.AST, name: str) -> bool:
    """Whether `node` is the one reviewed way to bind a `_KEYWORD_CALLEES`
    name: an undecorated `def _start(..., *, accept_rejections, ...)` with
    no `**kwargs`
    (`resume`'s own), or `ResumeInvocationRow` imported under its own name
    from the journal."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return (
            name in ("_start", "resume")
            and not node.decorator_list
            and node.args.kwarg is None
            and any(a.arg == FLAG for a in node.args.kwonlyargs)
        )
    if isinstance(node, ast.ImportFrom):
        alias = next(a for a in node.names if (a.asname or a.name) == name)
        return (
            name == "ResumeInvocationRow"
            and alias.asname is None
            and node.level == 0
            and node.module == JOURNAL_MODULE
        )
    return False


def _root_name(node: ast.expr) -> str | None:
    """The `Name` an attribute or subscript chain starts from
    (`ResumeInvocationRow` for `ResumeInvocationRow.__init__.__code__`), or
    `None` when it starts from anything else."""
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _is_unpacked(node: ast.expr, parents: dict[int, ast.AST]) -> bool:
    """Whether `node` is the source of a `**` unpacking, in a dict display
    (`{**x}`) or a call (`f(**x)`): a read of its items, never a write."""
    parent = parents.get(id(node))
    if isinstance(parent, ast.Dict):
        return any(k is None and v is node for k, v in zip(parent.keys, parent.values, strict=True))
    return isinstance(parent, ast.keyword) and parent.arg is None and parent.value is node


def _is_cli_pass(node: ast.keyword, parents: dict[int, ast.AST]) -> bool:
    """Rule 4's one reviewed exception, by exact shape: the keyword
    `accept_rejections=accept_rejections_flag` (a bare `Load` of `CLI_FLAG`)
    of a call to `paper_resume.resume`."""
    call = parents.get(id(node))
    return (
        node.arg == FLAG
        and isinstance(node.value, ast.Name)
        and node.value.id == CLI_FLAG
        and isinstance(node.value.ctx, ast.Load)
        and isinstance(call, ast.Call)
        and node in call.keywords
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "resume"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "paper_resume"
    )


def _arg_default(arguments: ast.arguments, arg: ast.arg) -> ast.expr | None:
    """The default of `arg` in `arguments`, or None when it has none."""
    positional = [*arguments.posonlyargs, *arguments.args]
    if arg in positional:
        offset = positional.index(arg) - (len(positional) - len(arguments.defaults))
        return arguments.defaults[offset] if offset >= 0 else None
    if arg in arguments.kwonlyargs:
        return arguments.kw_defaults[arguments.kwonlyargs.index(arg)]
    return None


def _is_resume_command(node: ast.AST | None) -> bool:
    """Whether `node` is a function decorated `@<app>.command("resume")`."""
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
        isinstance(d, ast.Call)
        and isinstance(d.func, ast.Attribute)
        and d.func.attr == "command"
        and not d.keywords
        and len(d.args) == 1
        and isinstance(d.args[0], ast.Constant)
        and d.args[0].value == "resume"
        for d in node.decorator_list
    )


def _cli_option(arg: ast.arg) -> ast.Call | None:
    """The `typer.Option("--accept-rejections", help=...)` call in `arg`'s
    `Annotated[bool, ...]` annotation, when it is exactly that, else None."""
    annotation = arg.annotation
    if not (
        isinstance(annotation, ast.Subscript)
        and isinstance(annotation.value, ast.Name)
        and annotation.value.id == "Annotated"
        and isinstance(annotation.slice, ast.Tuple)
        and len(annotation.slice.elts) == 2
        and isinstance(annotation.slice.elts[0], ast.Name)
        and annotation.slice.elts[0].id == "bool"
    ):
        return None
    option = annotation.slice.elts[1]
    if (
        isinstance(option, ast.Call)
        and isinstance(option.func, ast.Attribute)
        and option.func.attr == "Option"
        and isinstance(option.func.value, ast.Name)
        and option.func.value.id == "typer"
        and len(option.args) == 1
        and isinstance(option.args[0], ast.Constant)
        and option.args[0].value == "--accept-rejections"
        and {k.arg for k in option.keywords} == {"help", "allow_from_autoenv"}
        and len(option.keywords) == 2
        and any(
            k.arg == "allow_from_autoenv"
            and isinstance(k.value, ast.Constant)
            and k.value.value is False
            for k in option.keywords
        )
    ):
        return option
    return None


def _is_docstring(node: ast.Constant, parents: dict[int, ast.AST]) -> bool:
    expr = parents.get(id(node))
    owner = parents.get(id(expr))
    return (
        isinstance(expr, ast.Expr)
        and isinstance(owner, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and bool(owner.body)
        and owner.body[0] is expr
    )


#: `default_map` in Click's context settings defaults any option, the flag
#: included, behind its back (#1309).
DEFAULT_MAP = "default_map"
#: Callees `cli.py` may `**`-unpack into: the ledger row types, whose keys are
#: their own field names (`_set_rows`). Any other `**` could carry settings.
_UNPACK_CALLEES = frozenset({"row_type"})
#: Names that reach the Click command or context, or set attributes by a
#: computed name, so a default map could arrive another way; the attributes
#: `.main` (Click's entry, which takes context settings) and `.info` (a Typer
#: app's settings) as attributes only, so `cli.py`'s own `main()` still runs.
_CLICK_REACH = frozenset({"setattr", "getattr", "get_command", "make_context"})
_CLICK_ATTRIBUTES = frozenset({"main", "info", "context_settings", DEFAULT_MAP})


def _annotation_ids(tree: ast.Module) -> set[int]:
    """The ids of every node inside a parameter, return or field annotation."""
    roots: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
            roots.append(node.returns)
        elif isinstance(node, (ast.arg, ast.AnnAssign)) and node.annotation is not None:
            roots.append(node.annotation)
    return {id(n) for root in roots for n in ast.walk(root)}


def _app_default_misuses(tree: ast.Module, parents: dict[int, ast.AST]) -> list[str]:
    """#1309, fail closed: in `cli.py`, no default map can reach `resume`'s
    flag. `context_settings` is passed only to a `.command(...)` decorator
    (a command's own settings reach only it, and `resume`'s decorator takes no
    keyword), never assigned or read as an attribute; `Typer` is only called
    or annotated, never aliased or wrapped (`T = typer.Typer`, `partial`);
    `**` goes only into `_UNPACK_CALLEES`; nothing names `_CLICK_REACH`
    (`setattr`, `get_command(app).main(...)`, `app.info`); and `default_map`
    appears nowhere, as a keyword, attribute or string."""
    found = []
    annotations = _annotation_ids(tree)
    for node in ast.walk(tree):
        name = (
            node.id
            if isinstance(node, ast.Name)
            else node.attr
            if isinstance(node, ast.Attribute)
            else None
        )
        if isinstance(node, ast.Call):
            for k in node.keywords:
                if k.arg == "context_settings" and not (
                    isinstance(node.func, ast.Attribute) and node.func.attr == "command"
                ):
                    found.append(f"{node.lineno}: context_settings outside a command decorator")
                elif k.arg is None and not (
                    isinstance(node.func, ast.Name) and node.func.id in _UNPACK_CALLEES
                ):
                    found.append(f"{node.lineno}: ** into an unreviewed callee")
        elif isinstance(node, ast.keyword) and node.arg == DEFAULT_MAP:
            found.append(f"{node.value.lineno}: passes a {DEFAULT_MAP}")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and DEFAULT_MAP in node.value
        ):
            found.append(f"{node.lineno}: spells {DEFAULT_MAP}")
        if name in _CLICK_ATTRIBUTES and isinstance(node, ast.Attribute):
            found.append(f"{node.lineno}: names {name} as an attribute")
        elif name in _CLICK_REACH:
            found.append(f"{getattr(node, 'lineno', 0)}: names {name}")
        elif name == "Typer":
            parent = parents.get(id(node))
            called = isinstance(parent, ast.Call) and parent.func is node
            if not (called or id(node) in annotations):
                found.append(f"{getattr(node, 'lineno', 0)}: aliases or wraps Typer")
    return found


def _cli_flag_misuses(tree: ast.Module, parents: dict[int, ast.AST]) -> list[str]:
    """Rule 4's exception, fail closed: in `cli.py`, `CLI_FLAG` is bound exactly
    once, as a parameter whose default is the constant `False`, and loaded
    only as the value of the one reviewed pass (`_is_cli_pass`), at most once."""
    found = []
    params = []
    passes = 0
    commands: set[str] = set()
    options: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.arg == CLI_FLAG:
            command = parents.get(id(parents.get(id(node))))
            option = _cli_option(node)
            if not _is_resume_command(command):
                found.append(f"{node.lineno}: {CLI_FLAG} is not a `resume` command's parameter")
            else:
                commands.add(command.name)  # type: ignore[union-attr]
            if option is None:
                found.append(f"{node.lineno}: {CLI_FLAG} is not the bare reviewed typer.Option")
            else:
                options.append(option)
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == CLI_FLAG:
            found.append(f"{node.value.lineno}: passes {CLI_FLAG} by keyword")
        elif isinstance(node, ast.Name) and node.id in commands and isinstance(node.ctx, ast.Load):
            found.append(f"{node.lineno}: loads the resume command {node.id}")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and any(s in node.value for s in SPELLINGS)
            and not _is_docstring(node, parents)
            and not any(node is option.args[0] for option in options)
        ):
            found.append(f"{node.lineno}: spells the flag outside its option: {node.value!r}")
        for name, how in _bound(node):
            if name == CLI_FLAG and not isinstance(node, ast.arg):
                found.append(f"{getattr(node, 'lineno', 0)}: binds {CLI_FLAG} in {how}")
        if isinstance(node, ast.arguments):
            for arg in [*node.posonlyargs, *node.args, *node.kwonlyargs]:
                if arg.arg == CLI_FLAG:
                    params.append(arg)
                    default = _arg_default(node, arg)
                    if not (isinstance(default, ast.Constant) and default.value is False):
                        found.append(f"{arg.lineno}: {CLI_FLAG}'s default is not False")
            for loose in (node.vararg, node.kwarg):
                if loose is not None and loose.arg == CLI_FLAG:
                    found.append(f"{loose.lineno}: {CLI_FLAG} is a */** parameter")
        if isinstance(node, ast.keyword) and _is_cli_pass(node, parents):
            passes += 1
        elif isinstance(node, ast.Name) and node.id == CLI_FLAG:
            parent = parents.get(id(node))
            if not (isinstance(parent, ast.keyword) and _is_cli_pass(parent, parents)):
                found.append(f"{node.lineno}: unreviewed use of {CLI_FLAG}")
        elif isinstance(node, ast.Attribute) and node.attr == CLI_FLAG:
            found.append(f"{node.lineno}: names {CLI_FLAG} as an attribute")
    found.extend(_app_default_misuses(tree, parents))
    if len(params) > 1:
        found.append(f"{CLI_FLAG} is bound as {len(params)} parameters, not one")
    if passes > 1:
        found.append(f"{passes} reviewed passes of {CLI_FLAG}: rule 4 allows exactly one")
    return found


def misuses(tree: ast.Module, module: str = "") -> list[str]:
    """Rule 3, fail closed: a parameter that is not keyword-only (so no
    positional argument can feed it), a binding other than a keyword-only
    parameter or a bare annotated field, a PEP 695 type parameter named for
    it, a whole-string spelling, any attribute named for it, a keyword
    passing anything but the caller's own flag under `accept_rejections=`,
    or passing it to a callee outside `_KEYWORD_CALLEES` (or to a class
    keyword), any rebinding of `isinstance`, `type` or `bool` (the
    type-check exemption below only holds if those names are still the
    builtins) or any way of reaching the builtins or the module namespace
    to patch them, a rebinding of a reviewed callee in a module that passes
    the keyword, or (the catch-all) any `Load` of the bare name that is not
    the value of such a keyword, the checked argument of `resume`'s own
    `isinstance`/`type` call, or the whole `test` of an `if`. Anything else
    - smuggled through a display, a multiplied or generator expression, an
    `IfExp`, an alias bound first, another keyword, `del`, or laundered
    through any `**kwargs` collector - is reported rather than
    special-cased."""
    fields = {
        id(node.target)
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign) and node.value is None
    }
    passes_keyword = any(
        isinstance(node, ast.keyword) and node.arg == FLAG for node in ast.walk(tree)
    )
    pinned = _TYPE_CHECKS | _REACH | (_KEYWORD_CALLEES if passes_keyword else frozenset())
    parents = _parent_map(tree)
    cli = module == CLI_MODULE
    found = _cli_flag_misuses(tree, parents) if cli else []
    for node in ast.walk(tree):
        for name, how in _bound(node):
            if name in pinned and not (
                name in _KEYWORD_CALLEES and _is_reviewed_callee_binding(node, name)
            ):
                found.append(f"{getattr(node, 'lineno', 0)}: rebinds {name} in {how}")
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
            found.append(f"{node.lineno}: a star import can rebind {', '.join(sorted(pinned))}")
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and any(
            (node.module if isinstance(node, ast.ImportFrom) else alias.name) == "builtins"
            for alias in node.names
        ):
            found.append(f"{node.lineno}: imports from builtins")
        elif (
            isinstance(node, (ast.Attribute, ast.Subscript))
            and not isinstance(node.ctx, ast.Load)
            and (
                (isinstance(node, ast.Attribute) and node.attr in _TYPE_CHECKS | _KEYWORD_CALLEES)
                or _root_name(node) in _TYPE_CHECKS | _KEYWORD_CALLEES
            )
        ):
            found.append(f"{node.lineno}: patches {ast.unparse(node)}")
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in _REACH_ATTRIBUTES
            and not (node.attr == "__dict__" and _is_unpacked(node, parents))
        ):
            found.append(f"{node.lineno}: reaches a namespace through {ast.unparse(node)}")
        elif isinstance(node, ast.Name) and node.id in _REACH:
            found.append(f"{node.lineno}: reaches the namespace through {node.id}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "vars"
            and not node.args
        ):
            found.append(f"{node.lineno}: reaches the namespace through vars()")
        elif isinstance(node, ast.Attribute) and node.attr == FLAG:
            found.append(f"{node.lineno}: names the flag as an attribute: {ast.unparse(node)}")
        elif isinstance(node, ast.arguments):
            loose = [*node.posonlyargs, *node.args, node.vararg, node.kwarg]
            found += [
                f"{a.lineno}: not keyword-only" for a in loose if a is not None and a.arg == FLAG
            ]
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == FLAG
        ):
            found.append(f"{node.lineno}: binds the name in a def/class")
        elif isinstance(node, (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple)) and node.name == FLAG:
            found.append(f"{node.lineno}: binds the name in a type parameter")
        elif (isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == FLAG) or (
            isinstance(node, ast.MatchMapping) and node.rest == FLAG
        ):
            found.append(f"{node.lineno}: binds the name in a match")
        elif isinstance(node, ast.ExceptHandler) and node.name == FLAG:
            found.append(f"{node.lineno}: binds the name in an except")
        elif isinstance(node, (ast.Import, ast.ImportFrom)) and any(
            (alias.asname or alias.name) == FLAG for alias in node.names
        ):
            found.append(f"{node.lineno}: binds the name in an import")
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and FLAG in node.names:
            found.append(f"{node.lineno}: declares the name {type(node).__name__.lower()}")
        elif cli and isinstance(node, ast.keyword) and _is_cli_pass(node, parents):
            pass  # rule 4's one reviewed exception; `_cli_flag_misuses` pins it
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
                        and isinstance(keyword_callee, ast.Call)
                        and isinstance(keyword_callee.func, ast.Name)
                        and keyword_callee.func.id in _KEYWORD_CALLEES
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
    assert {name: found for name, tree in MODULES.items() if (found := misuses(tree, name))} == {}


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
        # #696: any `**kwargs` collector other than a reviewed callee.
        ("d = dict\n_start(c, *d(accept_rejections=accept_rejections).values())", misuses),
        ("_start(c, *OrderedDict(accept_rejections=accept_rejections).values())", misuses),
        (
            "_start(c, *vars(SimpleNamespace(accept_rejections=accept_rejections)).values())",
            misuses,
        ),
        (
            "def kw(**k):\n    return k\n_start(c, *kw(accept_rejections=accept_rejections))",
            misuses,
        ),
        ("class C(accept_rejections=accept_rejections): ...", misuses),
        ("_start = dict\n_start(accept_rejections=accept_rejections)", misuses),
        ("from m import dict as _start\n_start(accept_rejections=accept_rejections)", misuses),
        ("def _start(**kw): ...\n_start(accept_rejections=accept_rejections)", misuses),
        (
            "from m import ResumeInvocationRow\n"
            "ResumeInvocationRow(accept_rejections=accept_rejections)",
            misuses,
        ),
        ("resume._start = dict", misuses),
        # #696: the flag as an attribute, in any position.
        ("_start(c, row.accept_rejections)", misuses),
        ("row.accept_rejections = True", misuses),
        # #696: patching the builtins or the module namespace.
        ("import builtins\nbuiltins.isinstance = _start", misuses),
        ("__builtins__['bool'] = Spy", misuses),
        ("globals()['isinstance'] = _start", misuses),
        ("locals()", misuses),
        ("vars()['bool'] = Spy", misuses),
        ("b = __import__('builtins')", misuses),
        ("import builtins as b\nsetattr(b, 'bool', Spy)", misuses),
        ("from builtins import isinstance as i", misuses),
        ("import os\nos.__builtins__['isinstance'] = _start", misuses),
        ("_start.__globals__['bool'] = Spy", misuses),
        ("resume.__dict__['isinstance'] = _start", misuses),
        ("_start.__code__ = f.__code__", misuses),
        ("ResumeInvocationRow.__init__ = spy", misuses),
        ("ResumeInvocationRow.__init__.__code__ = g", misuses),
        ("_start.__kwdefaults__['x'] = 1", misuses),
        (
            "@spy\ndef _start(c, *, accept_rejections): ...\n"
            "_start(c, accept_rejections=accept_rejections)",
            misuses,
        ),
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
        "from tradepartner.store.journal import ResumeInvocationRow\n"
        "def _start(c, *, accept_rejections: bool): ...\n"
        "def resume(*, accept_rejections: bool):\n"
        "    if not isinstance(accept_rejections, bool):\n"
        "        raise TypeError(f'{type(accept_rejections).__name__}')\n"
        "    _start(c, accept_rejections=accept_rejections)\n"
        "    ResumeInvocationRow(accept_rejections=accept_rejections)\n"
        "    if accept_rejections:\n"
        "        pass\n"
        "class Row:\n"
        "    accept_rejections: bool\n"
    )
    assert misuses(allowed) == []
    assert defaults(allowed) == []


#: Rule 4's reviewed shape in `cli.py` (a Typer option under another name).
CLI_PASS = (
    '"""The module docstring may name --accept-rejections."""\n'
    '@paper_app.command("resume")\n'
    "def paper_resume_(\n"
    "    reason: str,\n"
    "    accept_rejections_flag: Annotated[\n"
    "        bool,\n"
    '        typer.Option("--accept-rejections", help="h", allow_from_autoenv=False),\n'
    "    ] = False,\n"
    ") -> None:\n"
    "    paper_resume.resume(s, c, b, k, reason, False, accept_rejections=accept_rejections_flag)\n"
)


def test_the_cli_exception_lets_exactly_its_reviewed_shape_through() -> None:
    assert misuses(ast.parse(CLI_PASS), CLI_MODULE) == []
    assert misuses(ast.parse(CLI_PASS))  # the same shape anywhere else is refused
    assert defaults(ast.parse(CLI_PASS)) == []
    # a command's own context settings stay open to the other commands (#1309)
    other = '@dataset_app.command("register", context_settings={"allow_extra_args": True})\n'
    assert misuses(ast.parse(CLI_PASS + other + "def register() -> None: ...\n"), CLI_MODULE) == []
    # the ledger rows' reviewed `**`, and `Typer` called or annotated (#1309)
    reviewed = (
        "def make_app() -> typer.Typer:\n    return typer.Typer(no_args_is_help=True)\n"
        "rows = [row_type(**dict(zip(names, row, strict=True))) for row in rows]\n"
    )
    assert misuses(ast.parse(CLI_PASS + reviewed), CLI_MODULE) == []


@pytest.mark.parametrize(
    "source",
    [
        # a second reviewed exception
        CLI_PASS + "    paper_resume.resume(s, accept_rejections=accept_rejections_flag)\n",
        CLI_PASS + "def again(accept_rejections_flag: bool = False) -> None: ...\n",
        # a literal or any other expression under the keyword
        CLI_PASS.replace("accept_rejections=accept_rejections_flag", "accept_rejections=True"),
        CLI_PASS.replace("=accept_rejections_flag)", "=not accept_rejections_flag)"),
        CLI_PASS.replace("=accept_rejections_flag)", "=bool(accept_rejections_flag))"),
        # another callee, another default, another binding or use
        CLI_PASS.replace("paper_resume.resume(", "other.resume("),
        CLI_PASS.replace("paper_resume.resume(", "paper_resume._start("),
        CLI_PASS.replace("] = False", "] = True"),
        CLI_PASS.replace("] = False", "]"),
        # the value's source: a direct call, alias or partial of the command
        CLI_PASS + "paper_resume_('r', accept_rejections_flag=True)\n",
        CLI_PASS + "paper_resume_('r', True)\n",
        CLI_PASS + "f = functools.partial(paper_resume_, accept_rejections_flag=True)\n",
        CLI_PASS + "alias = paper_resume_\n",
        # the app invoking itself with the flag, or the flag spelt elsewhere
        CLI_PASS + "app(['paper', 'resume', '--reason', 'r', '--accept-rejections'])\n",
        CLI_PASS + "ARGS = 'resume --accept-rejections'\n",
        # an envvar, callback, default or flag value on the option, or another option
        CLI_PASS.replace('help="h"', 'help="x", envvar="ACCEPT"'),
        CLI_PASS.replace('help="h"', 'help="x", callback=always_true'),
        CLI_PASS.replace('help="h"', 'help="x", flag_value=True'),
        CLI_PASS.replace('"--accept-rejections", help', '"--accept-rejections", "-a", help'),
        CLI_PASS.replace('"--accept-rejections"', '"--yes"'),
        CLI_PASS.replace(", allow_from_autoenv=False", ""),
        CLI_PASS.replace("allow_from_autoenv=False", "allow_from_autoenv=True"),
        CLI_PASS.replace('command("resume")', 'command("resume", context_settings=c)'),
        # app-level context settings, or a default map from anywhere (#1309)
        CLI_PASS + "paper_app = typer.Typer(context_settings={'default_map': m})\n",
        CLI_PASS + "paper_app = typer.Typer(context_settings=SETTINGS)\n",
        CLI_PASS + "paper_app = Typer(context_settings=SETTINGS)\n",
        CLI_PASS + "paper_app = typer.Typer(**OPTIONS)\n",
        CLI_PASS + "app.add_typer(paper_app, name='paper', context_settings=c)\n",
        CLI_PASS + "app.add_typer(paper_app, **OPTIONS)\n",
        CLI_PASS + "@paper_app.callback(context_settings=c)\ndef root() -> None: ...\n",
        CLI_PASS + "@paper_app.callback(**OPTIONS)\ndef root() -> None: ...\n",
        CLI_PASS + "@app.callback()\ndef root(ctx: typer.Context) -> None:\n"
        "    ctx.default_map = load()\n",
        CLI_PASS + "    ctx.default_map.update(m)\n",
        CLI_PASS + "make_app()(default_map=m)\n",
        CLI_PASS + "OPTIONS = {'default_map': m}\n",
        CLI_PASS + "OPTIONS = dict(default_map=m)\n",
        CLI_PASS + "paper_app.info.context_settings = SETTINGS\n",
        CLI_PASS + "T = typer.Typer\npaper_app = T(context_settings=SETTINGS)\n",
        CLI_PASS + "paper_app = functools.partial(typer.Typer, context_settings=SETTINGS)()\n",
        CLI_PASS + "paper_app = functools.partial(Typer)()\n",
        CLI_PASS + "make_app()(**OPTIONS)\n",
        CLI_PASS + "typer.main.get_command(app).main(**OPTIONS)\n",
        CLI_PASS + "cmd = typer.main.get_command(app)\ncmd.context_settings = SETTINGS\n",
        CLI_PASS + "setattr(ctx, 'default_' + 'map', m)\n",
        CLI_PASS + "make_app().info = INFO\n",
        # not a `resume` command's parameter
        CLI_PASS.replace('command("resume")', 'command("run")'),
        CLI_PASS.replace('@paper_app.command("resume")\n', ""),
        CLI_PASS + "    accept_rejections_flag = True\n",
        CLI_PASS + "    x = accept_rejections_flag\n",
        CLI_PASS + "    other(flag=accept_rejections_flag)\n",
    ],
)
def test_the_cli_exception_refuses_anything_but_its_one_shape(source: str) -> None:
    assert misuses(ast.parse(source), CLI_MODULE), source


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


def test_every_cli_refusal_sample_changes_the_reviewed_shape() -> None:
    marks = test_the_cli_exception_refuses_anything_but_its_one_shape.pytestmark  # type: ignore[attr-defined]
    samples = next(m.args[1] for m in marks if m.name == "parametrize")
    assert all(sample != CLI_PASS for sample in samples)

"""The model boundary, held by tests (ADR 0008 point 2; ADR 0013 point 3 (a) to (e)).

Research-labeling spec reqs 1 and 12 to 16, as its 2026-10-06 amendment (C9, C13)
reads them. Each test runs over the real tree and over the fixtures in
`tests/fixtures/llm_boundary/` that it must fail on:

- **(a) dependencies**: no model client in `project.dependencies`; a `research`
  dependency group only while the labeling spec is Accepted, holding
  `typesafe-sdk` only; no listed name in any other group. ADR 0008 point 2's
  check (no `tradepartner.llm` unless `docs/specs/llm-analyst.md` is Accepted)
  sits beside it.
- **(b) imports**: direct imports over `src/` and `scripts/`, relative imports
  resolved, `import_module` and `__import__` read, cases (i) to (v).
- **(c) text**: the vendor host and key name, table and dataset names in the
  packet modules, and the research store's directory, each only where allowed; the
  host rule also reads `tests/` (#1031).
- **(d) store unchanged** and **(e) the zero default** exercise modules later
  tasks land: (e) runs since T119 landed `research.models`; (d) skips with
  `labeling modules pending` until T123 and T123b remove its skips. The snapshot (d) relies on and
  the recordings' contract (e) are pinned here already.

Known limit: the text rules read literal text, so a string split across a
concatenation (`"typesafe" + ".ai"`) passes them; reviewers read diffs for that.
"""

from __future__ import annotations

import ast
import bz2
import contextlib
import functools
import gzip
import io
import json
import lzma
import re
import subprocess
import sys
import tokenize
import tomllib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn

import duckdb
import pytest
from conftest import load_universe_fixtures

from tradepartner.store.db import configure_connection
from tradepartner.store.lab_schema import apply_lab_schema
from tradepartner.store.schema import (
    JOURNAL_TABLE_NAMES,
    LATER_JOURNAL_TABLE_NAMES,
    MASTER_CHECK_TABLE_NAMES,
    REGISTRY_TABLE_NAMES,
    RESEARCH_TABLE_NAMES,
    TABLE_NAMES,
    init_schema,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "llm_boundary"
TYPESAFE_RECORDINGS = REPO / "tests" / "fixtures" / "typesafe"
LABELING_SPEC = REPO / "docs" / "specs" / "research-labeling.md"
LLM_ANALYST_SPEC = REPO / "docs" / "specs" / "llm-analyst.md"
SCANNED_ROOTS = ("src", "scripts")
#: (c)'s host rule also reads `tests/` (#1031), so a fake or fixture cannot name the
#: vendor host unseen.
TESTS_ROOT = "tests"

RECORDINGS_PENDING = "owner recordings pending"


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


GZIP_MAGIC = b"\x1f\x8b"
#: Compressed formats the text scan cannot read (#1085), by their leading bytes:
#: zip, bzip2, xz, zstd, 7z, lz4, Unix compress (`.Z`), lzip and lzma. Any file under
#: a scanned root that starts with one, whatever its name, fails closed, since a host
#: inside it would go unseen. Gzip is read decompressed instead (`_read`).
OPAQUE_MAGICS = (
    b"PK\x03\x04",
    b"PK\x05\x06",
    *(b"BZh" + bytes([digit]) for digit in b"123456789"),
    b"\xfd7zXZ\x00",
    b"\x28\xb5\x2f\xfd",
    b"7z\xbc\xaf\x27\x1c",
    b"\x04\x22\x4d\x18",
    b"\x1f\x9d",
    b"LZIP",
    b"\x5d\x00\x00",
)
#: Formats with no reliable leading bytes (brotli), or named as an archive: refused by
#: name too.
OPAQUE_SUFFIXES = frozenset({".zip", ".bz2", ".xz", ".zst", ".7z", ".lz4", ".br", ".lzma"})


def _decompressed(path: Path) -> bytes:
    """A file's bytes with every gzip layer removed, detected by content, not by name
    (#1085): `.GZ`, `.tgz`, a gzip named `.json` and a `.gz.gz` are all unwrapped."""
    data = path.read_bytes()
    while data.startswith(GZIP_MAGIC):
        data = gzip.decompress(data)
    return data


def _read(path: Path) -> str:
    """A file's text as the scans see it: gzip removed first (#1085), so a gzipped
    fixture cannot hide the vendor host; undecodable bytes are replaced."""
    return _decompressed(path).decode("utf-8", errors="replace")


def _opaque(path: Path) -> bool:
    """Whether the scan cannot read `path`: another compressed format, by its leading
    bytes after any gzip layer or by its name."""
    return path.suffix.lower() in OPAQUE_SUFFIXES or _decompressed(path).startswith(OPAQUE_MAGICS)


def _real_tree(
    suffix: str | None = ".py", roots: tuple[str, ...] = SCANNED_ROOTS
) -> dict[str, str]:
    """Every file under `roots` (`src/` and `scripts/` by default; only `suffix`
    files when given), keyed by its repo-relative POSIX path."""
    tree: dict[str, str] = {}
    for root in roots:
        for path in sorted((REPO / root).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if suffix is not None and path.suffix != suffix:
                continue
            tree[_rel(path)] = _read(path)
    return tree


@dataclass(frozen=True)
class Case:
    id: str
    path: str
    source: str
    rule: str


def _cases(name: str) -> list[Case]:
    doc = tomllib.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return [Case(**case) for case in doc["case"]]


def _with(tree: Mapping[str, str], case: Case) -> dict[str, str]:
    """`tree` with the case's snippet appended to its file (or as a new file)."""
    changed = dict(tree)
    base = changed.get(case.path, "")
    changed[case.path] = f"{base}\n{case.source}" if base else case.source
    return changed


# --- (a) dependencies (spec req 12; ADR 0008 point 2) ---------------------------------

#: Spec req 12's list; adding a name is a spec amendment.
MODEL_CLIENT_PACKAGES = frozenset(
    {
        "typesafe-sdk",
        "anthropic",
        "openai",
        "google-generativeai",
        "cohere",
        "mistralai",
        "transformers",
        "sentence-transformers",
        "torch",
    }
)
#: What a `research` group may hold while the labeling spec is Accepted.
RESEARCH_GROUP_ALLOWED = frozenset({"typesafe-sdk"})
ACCEPTED_PREFIX = "**Status:** Accepted"


def _status_line(spec: Path) -> str | None:
    """The spec's `**Status:**` header line, or None when the spec is absent."""
    if not spec.exists():
        return None
    for line in spec.read_text(encoding="utf-8").splitlines():
        if line.startswith("**Status:**"):
            return line
    return ""


def _accepted(status_line: str | None) -> bool:
    return status_line is not None and status_line.startswith(ACCEPTED_PREFIX)


def _requirement_name(requirement: object) -> str:
    """The normalised distribution name of a PEP 508 requirement string (an
    `{include-group = ...}` table has none)."""
    if not isinstance(requirement, str):
        return ""
    name = re.split(r"[\s\[<>=!~;@(]", requirement.strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def dependency_violations(pyproject: Mapping[str, Any], labeling_status: str | None) -> list[str]:
    """Test (a) over a parsed `pyproject.toml`, given the labeling spec's status line."""
    found: list[str] = []
    project = pyproject.get("project", {})
    runtime = {_requirement_name(r) for r in project.get("dependencies", [])}
    for name in sorted(runtime & MODEL_CLIENT_PACKAGES):
        found.append(f"project.dependencies holds the model client {name!r}")
    groups: dict[str, list[object]] = dict(pyproject.get("dependency-groups", {}))
    for extra, reqs in project.get("optional-dependencies", {}).items():
        groups[f"optional-dependencies.{extra}"] = reqs
    uv_dev = pyproject.get("tool", {}).get("uv", {}).get("dev-dependencies")
    if uv_dev is not None:
        groups["tool.uv.dev-dependencies"] = uv_dev
    for group, reqs in sorted(groups.items()):
        names = {_requirement_name(r) for r in reqs}
        includes = {r.get("include-group") for r in reqs if isinstance(r, dict)}
        if group != "research" and "research" in includes:
            found.append(f"dependency group {group!r} includes the research group")
        if group == "research":
            if not _accepted(labeling_status):
                found.append(
                    "a `research` dependency group exists while docs/specs/research-labeling.md "
                    f"is not Accepted (status line: {labeling_status!r})"
                )
            for name in sorted(names & MODEL_CLIENT_PACKAGES - RESEARCH_GROUP_ALLOWED):
                found.append(f"the research group holds {name!r}, not typesafe-sdk")
            for name in sorted(names - MODEL_CLIENT_PACKAGES - {""}):
                found.append(f"the research group holds {name!r}, not typesafe-sdk")
        else:
            for name in sorted(names & MODEL_CLIENT_PACKAGES):
                found.append(f"dependency group {group!r} holds the model client {name!r}")
    return found


def llm_package_violations(llm_package_exists: bool, llm_spec_status: str | None) -> list[str]:
    """ADR 0008 point 2: `tradepartner.llm` exists only once the Phase 5 spec is Accepted
    at its recorded path; the check keys on the spec, not on the package."""
    if llm_package_exists and not _accepted(llm_spec_status):
        return ["tradepartner.llm exists while docs/specs/llm-analyst.md is not Accepted"]
    return []


def _pyproject(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_real_pyproject_passes_test_a() -> None:
    assert (
        dependency_violations(_pyproject(REPO / "pyproject.toml"), _status_line(LABELING_SPEC))
        == []
    )


def test_real_tree_passes_adr_0008_package_check() -> None:
    llm = REPO / "src" / "tradepartner" / "llm"
    exists = llm.exists() or llm.with_suffix(".py").exists()
    assert llm_package_violations(exists, _status_line(LLM_ANALYST_SPEC)) == []


DRAFT = "**Status:** Draft  ·  **Issue:** #963"
ACCEPTED = "**Status:** Accepted (owner, 2026-10-05, #964); amended 2026-10-06"


@pytest.mark.parametrize(
    ("fixture", "status", "fails"),
    [
        ("pyproject_runtime_anthropic.toml", ACCEPTED, True),
        ("pyproject_runtime_anthropic.toml", DRAFT, True),
        ("pyproject_research_typesafe.toml", DRAFT, True),
        ("pyproject_research_typesafe.toml", None, True),
        ("pyproject_research_typesafe.toml", ACCEPTED, False),
        ("pyproject_research_openai.toml", DRAFT, True),
        ("pyproject_research_openai.toml", ACCEPTED, True),
        ("pyproject_dev_torch.toml", ACCEPTED, True),
        ("pyproject_dev_includes_research.toml", ACCEPTED, True),
    ],
)
def test_a_fixture_pyprojects(fixture: str, status: str | None, fails: bool) -> None:
    assert bool(dependency_violations(_pyproject(FIXTURES / fixture), status)) is fails


def test_a_reads_the_real_labeling_spec_header_prefix() -> None:
    status = _status_line(LABELING_SPEC)
    assert status is not None and status.startswith(ACCEPTED_PREFIX), status


@pytest.mark.parametrize(
    ("exists", "status", "fails"),
    [
        (True, None, True),
        (True, "**Status:** Draft", True),
        (True, "**Status:** Accepted (owner)", False),
        (False, None, False),
    ],
)
def test_adr_0008_package_check_keys_on_the_spec(
    exists: bool, status: str | None, fails: bool
) -> None:
    assert bool(llm_package_violations(exists, status)) is fails


# --- (b) imports (spec req 13 as C13 amends it) ---------------------------------------

RESEARCH = "tradepartner.research"
MODELS = "tradepartner.research.models"
#: C13: the one module that may import `research.models`.
MODELS_IMPORTER = "tradepartner.research.labeling.job"
#: C13: the review page imports the review modules and nothing else of the boundary
#: (never `job`, `models` or `datafiles`). T123b's split trigger fired (#1177): the
#: gold module beside `review` (T131) joins this set.
REVIEW_PAGE = "tradepartner.research.labeling.review_page"
REVIEW_MODULES = frozenset(
    {"tradepartner.research.labeling.review", "tradepartner.research.labeling.gold"}
)
#: (i)'s exceptions, each with the `tradepartner.research` modules it may import
#: (None: any). `tradepartner.cli` is the spec's. `tradepartner.store.research` is the
#: registry API, which T81 (#1007) built on `research.RunHandle`, `load_dataset`,
#: `experiment` and `gates` after ADR 0013 was written (an open question on #1028);
#: it may import those modules only, never the labeling code or `research.models`.
RESEARCH_IMPORTERS: Mapping[str, frozenset[str] | None] = {
    "tradepartner.cli": None,
    "tradepartner.store.research": frozenset(
        {RESEARCH, f"{RESEARCH}.experiment", f"{RESEARCH}.gates"}
    ),
}
#: The facades the dashboard and the backtest read, which must not load the model
#: code even transitively (checked in a subprocess).
FACADES = (
    RESEARCH,
    "tradepartner.store.research",
    "tradepartner.store.registry",
    "tradepartner.backtest.results",
)
#: (ii): network clients only `research.models` may import under `tradepartner.research`
#: (#1031: the standard library's and the common third-party ones, beside httpx;
#: widened by #1121 (C13, the recommended option on #1085) to the TLS, event-loop,
#: other-protocol clients and `subprocess`, since a shell runs `curl`).
NETWORK_CLIENTS = (
    "httpx",
    "urllib.request",
    "http.client",
    "socket",
    "requests",
    "aiohttp",
    "ssl",
    "asyncio",
    "urllib3",
    "httplib2",
    "websockets",
    "ftplib",
    "smtplib",
    "xmlrpc.client",
    "subprocess",
)
#: (ii): parents of a listed client. `import urllib` or `import urllib.parse` binds
#: `urllib`, and a `*` import too, so each reaches the client as an attribute.
NETWORK_PARENTS = frozenset({"urllib", "http", "xmlrpc"})
#: (ii): a listed client one named module may import, by name (C13, #1121): the frame
#: build runs `git rev-parse` for its code version. `os.system`, `os.popen` and
#: `os.exec*` are the same route and stay unbanned (`os` is imported throughout): a
#: known gap that review catches and this test does not.
NETWORK_CLIENT_ALLOWED: Mapping[str, frozenset[str]] = {
    "subprocess": frozenset({"tradepartner.research.labeling.frame"}),
}
#: (iii): what `tradepartner.research` may never import, whatever the name.
RESEARCH_FORBIDDEN = (
    "tradepartner.adapters",
    "tradepartner.ingest",
    "tradepartner.repair",
    "tradepartner.retract",
    "tradepartner.backfill",
    "tradepartner.collect",
)
#: (iii): store modules `tradepartner.research` may import whole (the as-of readers,
#: the table-name constants, and the registry API its writes go through).
STORE_WHOLE = frozenset(
    {"tradepartner.store.asof", "tradepartner.store.schema", "tradepartner.store.research"}
)
#: (iii): the reader names it may import from any other store module.
STORE_READERS: Mapping[str, frozenset[str]] = {
    #: `StoreLockedError` is the exception class only (T131's lock maps it to "store
    #: busy" on the caller's connection); never `open_for_write`.
    "tradepartner.store.db": frozenset(
        {"open_read_only", "utc_now", "ensure_tz_aware", "StoreLockedError"}
    ),
    "tradepartner.store.delistings": frozenset({"listing_ends_as_of", "delistings_as_of"}),
    "tradepartner.store.master": frozenset({"securities_as_of", "primary_security_id"}),
}
_DYNAMIC = frozenset({"import_module", "__import__"})
#: Loaders whose argument is a path, not a module name: never used under `src/`.
_PATH_LOADERS = frozenset({"spec_from_file_location", "SourceFileLoader"})
#: The target of an import the scan cannot read (a non-literal module name).
UNREADABLE = "<unreadable>"


def _under(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


def _module_of(path: str) -> str:
    """`src/tradepartner/a/b.py` -> `tradepartner.a.b`; `scripts/x.py` -> `scripts.x`."""
    parts = list(Path(path).with_suffix("").parts)
    if parts[0] == "src":
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve(importer: str, is_package: bool, level: int, module: str | None) -> str:
    """The absolute module a relative import names."""
    if level == 0:
        return module or ""
    base = importer.split(".")
    if not is_package:
        base = base[:-1]
    base = base[: len(base) - (level - 1)]
    return ".".join([*base, module] if module else base)


@dataclass(frozen=True)
class Edge:
    """One direct import: `target` a module, `name` the name taken from it (`from
    target import name`) or None for a module import."""

    importer: str
    target: str
    name: str | None


def _edges(path: str, source: str) -> Iterator[Edge]:
    importer = _module_of(path)
    is_package = path.endswith("__init__.py")
    tree = ast.parse(source, filename=path)
    loaders = set(_DYNAMIC | _PATH_LOADERS)
    for node in ast.walk(tree):  # `from importlib import import_module as load`
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("importlib"):
            loaders.update(a.asname for a in node.names if a.asname and a.name in loaders)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield Edge(importer, alias.name, None)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(importer, is_package, node.level, node.module)
            for alias in node.names:
                yield Edge(importer, base, alias.name)
        elif isinstance(node, ast.Call):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if called not in loaders:
                continue
            args = [*node.args[:1], *(k.value for k in node.keywords if k.arg == "name")]
            first = args[0] if args else None
            literal = isinstance(first, ast.Constant) and isinstance(first.value, str)
            if called in _PATH_LOADERS or not literal:
                yield Edge(importer, UNREADABLE, called)
                continue
            assert isinstance(first, ast.Constant)
            target = str(first.value)
            if target.startswith("."):
                level = len(target) - len(target.lstrip("."))
                target = _resolve(importer, is_package, level, target.lstrip(".") or None)
            yield Edge(importer, target, None)


def _targets(edge: Edge) -> tuple[str, ...]:
    """The modules an edge may reach: `from a import b` may import module `a.b`."""
    if edge.name is None or edge.name == "*":
        return (edge.target,)
    return (edge.target, f"{edge.target}.{edge.name}")


def _store_import_allowed(edge: Edge) -> bool:
    """(iii): may `tradepartner.research` take this from `tradepartner.store`? A bare
    `tradepartner.store` would reach every store module as an attribute, so it may not."""
    if edge.target in ("tradepartner", "tradepartner.store") and edge.name is not None:
        module, name = f"{edge.target}.{edge.name}", None
    else:
        module, name = edge.target, edge.name
    if not _under(module, "tradepartner.store"):
        return True
    if module in STORE_WHOLE:
        return True
    return name is not None and name in STORE_READERS.get(module, frozenset())


def _research_import_allowed(edge: Edge, modules: frozenset[str]) -> bool:
    """(i): is this import of `tradepartner.research` one of the listed exceptions?"""
    if edge.importer not in RESEARCH_IMPORTERS:
        return False
    allowed = RESEARCH_IMPORTERS[edge.importer]
    if allowed is None:
        return True
    reached = [
        t for t in _targets(edge) if _under(t, RESEARCH) and (t in modules or t == edge.target)
    ]
    return all(t in allowed for t in reached)


def _review_page_allowed(edge: Edge) -> bool:
    """C13 (ii): `review_page` takes from `tradepartner.research` only the review modules
    (`from ..labeling import review`, `from .review import x`, `import ...review`)."""
    if edge.name is not None and f"{edge.target}.{edge.name}" in REVIEW_MODULES:
        return True
    return edge.target in REVIEW_MODULES


def import_violations(tree: Mapping[str, str]) -> list[tuple[str, str]]:
    """Every (rule, message) the import scan reports over `tree` (path -> source)."""
    found: list[tuple[str, str]] = []
    modules = frozenset(_module_of(p) for p in tree)
    for path, source in sorted(tree.items()):
        script = path.startswith("scripts/")
        for edge in _edges(path, source):
            src = edge.importer
            targets = _targets(edge)

            def hits(package: str, targets: tuple[str, ...] = targets) -> bool:
                return any(_under(t, package) for t in targets)

            def report(rule: str, what: str, edge: Edge = edge, path: str = path) -> None:
                name = f" ({edge.name})" if edge.name else ""
                found.append((rule, f"{path}: {what}: {edge.target}{name}"))

            in_research = _under(src, RESEARCH)
            if edge.target == UNREADABLE:
                if not script:
                    report("dynamic", "a dynamic import the scan cannot read")
                continue
            if hits("tradepartner.llm") and not _under(src, "tradepartner.llm"):
                report("adr0008", "imports tradepartner.llm outside it")
            if (
                not script
                and hits(RESEARCH)
                and not in_research
                and not _research_import_allowed(edge, modules)
            ):
                report("i", "imports tradepartner.research from outside it and cli")
            parent = edge.target.split(".")[0] in NETWORK_PARENTS
            bare_parent = parent and (edge.name is None or edge.name == "*")
            banned = [
                c for c in NETWORK_CLIENTS if src not in NETWORK_CLIENT_ALLOWED.get(c, frozenset())
            ]
            if in_research and src != MODELS and (bare_parent or any(map(hits, banned))):
                report("ii", "imports a network client under tradepartner.research outside models")
            if hits(MODELS) and src not in (MODELS, MODELS_IMPORTER):
                report("ii", f"imports research.models; only {MODELS_IMPORTER} may")
            if src == REVIEW_PAGE and hits(RESEARCH) and not _review_page_allowed(edge):
                report("ii", "review_page imports the boundary beyond the review modules")
            if in_research:
                if any(hits(p) for p in RESEARCH_FORBIDDEN):
                    report("iii", "tradepartner.research imports an adapter or writer")
                if not _store_import_allowed(edge):
                    report("iii", "tradepartner.research imports a store name not allowlisted")
                if hits("tradepartner.execution"):
                    report("iv", "tradepartner.research imports tradepartner.execution")
                if hits("tradepartner.corpus"):
                    report("v", "tradepartner.research imports tradepartner.corpus")
            if _under(src, "tradepartner.execution") and hits(RESEARCH):
                report("iv", "tradepartner.execution imports tradepartner.research")
            if script and (hits(RESEARCH) or hits("tradepartner.store.research")):
                report("iv", "a script imports the research modules")
            if _under(src, "tradepartner.corpus") and hits(RESEARCH):
                report("v", "tradepartner.corpus imports tradepartner.research")
    return found


def test_real_tree_passes_test_b() -> None:
    assert import_violations(_real_tree()) == []


@pytest.mark.parametrize("case", _cases("import_cases.toml"), ids=lambda c: c.id)
def test_b_fixture_snippets(case: Case) -> None:
    rules = {rule for rule, _ in import_violations(_with(_real_tree(), case))}
    if case.rule:
        assert case.rule in rules, rules
    else:
        assert rules == set()


def test_b_facades_do_not_load_the_model_code() -> None:
    """The scan is direct-only; this pins the transitive closure of the modules the
    dashboard and the backtest read: none loads `research.models` or the labeling code."""
    code = (
        "import importlib, sys\n"
        f"for m in {FACADES!r}: importlib.import_module(m)\n"
        "print('\\n'.join(sorted(sys.modules)))\n"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=REPO
    ).stdout.split()
    assert [m for m in loaded if _under(m, MODELS) or _under(m, f"{RESEARCH}.labeling")] == []


def test_b_resolves_relative_imports() -> None:
    edge = next(_edges("src/tradepartner/research/labeling/crosswalk.py", "from ..models import x"))
    assert (edge.target, edge.name) == (MODELS, "x")
    edge = next(_edges("src/tradepartner/research/labeling/__init__.py", "from . import job"))
    assert _targets(edge)[-1] == "tradepartner.research.labeling.job"


# --- (c) text (spec req 14 as C13 amends it) ------------------------------------------

#: Under `tests/` only the host itself: a test may point `api_base_url` at a fake.
_HOST_NAME = re.compile(r"typesafe\.ai", re.IGNORECASE)
_HOST = re.compile(rf"{_HOST_NAME.pattern}|\bapi_base_url\b", re.IGNORECASE)
KEY_NAME = "TYPESAFE_API_KEY"
#: The env name and the settings attribute that reads it (`typesafe_api_key`).
_KEY = re.compile(KEY_NAME, re.IGNORECASE)
#: Where the vendor host (and `research.labeling.api_base_url`, its config key) and the
#: key's name may appear (and `.env.example`, outside the scanned roots).
VENDOR_FILES = frozenset({"src/tradepartner/research/models.py", "src/tradepartner/config.py"})
#: Where the vendor host may appear under `tests/`: this test's text fixtures and the
#: tests of the two vendor files (this file names it only as a pattern, so is scanned).
TEST_HOST_FILES = frozenset(
    {
        "tests/fixtures/llm_boundary/text_cases.toml",
        "tests/research/labeling/test_models.py",
        "tests/test_config.py",
    }
)
PACKET_MODULES = frozenset(
    {
        "src/tradepartner/research/labeling/packets.py",
        "src/tradepartner/research/labeling/questions.py",
    }
)
#: Spec req 14: the allowlist is empty.
PACKET_TABLE_ALLOWLIST: frozenset[str] = frozenset()
STORE_TABLES = (
    frozenset(
        TABLE_NAMES
        + JOURNAL_TABLE_NAMES
        + LATER_JOURNAL_TABLE_NAMES
        + MASTER_CHECK_TABLE_NAMES
        + REGISTRY_TABLE_NAMES
        + RESEARCH_TABLE_NAMES
    )
    - PACKET_TABLE_ALLOWLIST
)
OWNER_DATASETS = ("departure-reason-gold", "departure-reason-reviews")
_TABLE_TOKEN = re.compile(
    r"\b(" + "|".join(sorted(map(re.escape, STORE_TABLES), key=len, reverse=True)) + r")\b"
)
DATA_DIR_FILES = frozenset(
    {
        "src/tradepartner/config.py",
        "src/tradepartner/research/datafiles.py",
        "src/tradepartner/research/__init__.py",
    }
)
_DATA_DIR = re.compile(r"data/research|research\.data_dir")
_WORDS = frozenset({tokenize.NAME, tokenize.STRING, tokenize.FSTRING_MIDDLE})


def _identifiers_and_strings(source: str) -> Iterator[str]:
    """Every identifier and string-literal token of a Python source (no comments)."""
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in _WORDS:
            yield token.string


@functools.cache
def _text_tree() -> Mapping[str, str]:
    """What the text scan reads: every file under `src/`, `scripts/` and `tests/`,
    read once and read-only (`_with` copies it)."""
    return MappingProxyType(_real_tree(suffix=None, roots=(*SCANNED_ROOTS, TESTS_ROOT)))


def text_violations(tree: Mapping[str, str]) -> list[tuple[str, str]]:
    """Every (rule, message) the text scan reports over `tree` (path -> text). Under
    `tests/` only the host rule applies, outside `TEST_HOST_FILES`."""
    found: list[tuple[str, str]] = []
    for path, text in sorted(tree.items()):
        if path.startswith(f"{TESTS_ROOT}/"):
            if path not in TEST_HOST_FILES and _HOST_NAME.search(text):
                found.append(("host", f"{path} names the vendor host"))
            continue
        if path not in VENDOR_FILES:
            if _HOST.search(text):
                found.append(("host", f"{path} names the vendor host"))
            if _KEY.search(text):
                found.append(("key", f"{path} names {KEY_NAME}"))
        if path not in DATA_DIR_FILES and _DATA_DIR.search(text):
            found.append(("data_dir", f"{path} names the research store's directory"))
        if path in PACKET_MODULES:
            for word in _identifiers_and_strings(text):
                for table in _TABLE_TOKEN.findall(word):
                    found.append(("table", f"{path} names the table {table!r}"))
                for dataset in OWNER_DATASETS:
                    if dataset in word:
                        found.append(("dataset", f"{path} names the dataset {dataset!r}"))
    return found


def test_real_tree_passes_test_c() -> None:
    assert text_violations(_text_tree()) == []


@pytest.mark.parametrize("case", _cases("text_cases.toml"), ids=lambda c: c.id)
def test_c_fixture_snippets(case: Case) -> None:
    rules = {rule for rule, _ in text_violations(_with(_text_tree(), case))}
    if case.rule:
        assert case.rule in rules, rules
    else:
        assert rules == set()


#: Built from the pattern: this file is scanned and must not name the host.
_RECORDED_HOST = f'{{"url": "https://api.{_HOST_NAME.pattern.replace(chr(92), "")}/v1"}}'


@pytest.mark.parametrize(
    ("name", "layers"),
    [("recording.json.gz", 1), ("RECORDING.JSON.GZ", 1), ("recording.json", 1), ("r.gz.gz", 2)],
)
def test_c_reads_a_gzipped_fixture_decompressed(tmp_path: Path, name: str, layers: int) -> None:
    data = _RECORDED_HOST.encode()
    for _ in range(layers):
        data = gzip.compress(data)
    recording = tmp_path / name
    recording.write_bytes(data)
    assert not _HOST_NAME.search(data.decode("utf-8", errors="replace"))
    found = text_violations({f"tests/fixtures/typesafe/{name}": _read(recording)})
    assert ("host", f"tests/fixtures/typesafe/{name} names the vendor host") in found
    assert not _opaque(recording)


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("recording.json", bz2.compress(_RECORDED_HOST.encode())),
        ("recording.json", lzma.compress(_RECORDED_HOST.encode())),
        ("recording.json.gz", gzip.compress(bz2.compress(_RECORDED_HOST.encode()))),
        ("recording.ZIP", b"anything"),
        ("recording.br", b"anything"),
    ],
)
def test_c_refuses_a_compressed_file_it_cannot_read(tmp_path: Path, name: str, data: bytes) -> None:
    path = tmp_path / name
    path.write_bytes(data)
    assert _opaque(path)


def test_c_no_compressed_file_the_scan_cannot_read() -> None:
    opaque = [
        _rel(path)
        for root in (*SCANNED_ROOTS, TESTS_ROOT)
        for path in (REPO / root).rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and _opaque(path)
    ]
    assert opaque == []


def test_c_table_match_is_a_whole_token() -> None:
    assert _TABLE_TOKEN.findall("securities") == ["securities"]
    assert _TABLE_TOKEN.findall("security") == []


# --- (d) store unchanged (spec req 15 as C13 amends it) -------------------------------

#: The research tables a labeling run and its review may add rows to (req 15).
RESEARCH_WRITES = frozenset(
    {"research_runs", "research_results", "research_datasets", "research_decisions"}
)
PLANTED_WRITE = (FIXTURES / "planted_listings_write.sql").read_text(encoding="utf-8")


def table_snapshot(conn: duckdb.DuckDBPyConnection) -> dict[str, tuple[int, str]]:
    """Row count and an order-independent content hash of every table."""
    tables = [row[0] for row in conn.execute("SHOW TABLES").fetchall()]
    snapshot: dict[str, tuple[int, str]] = {}
    for table in tables:
        rows = sorted(repr(row) for row in conn.execute(f'SELECT * FROM "{table}"').fetchall())
        snapshot[table] = (len(rows), sha256("\n".join(rows).encode()).hexdigest())
    return snapshot


def changed_tables(
    before: Mapping[str, tuple[int, str]], after: Mapping[str, tuple[int, str]]
) -> set[str]:
    return {t for t in before.keys() | after.keys() if before.get(t) != after.get(t)}


def file_hashes(root: Path, *, skip: tuple[Path, ...]) -> dict[str, str]:
    """SHA-256 of every file under `root` outside `skip` (the events file and any
    other file a run must not touch)."""
    return {
        _rel_to(path, root): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not any(path == s or s in path.parents for s in skip)
    }


def _rel_to(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def tracked_changes() -> str:
    """`git status --porcelain`, new untracked files included (ignored ones are not)."""
    return subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _fixture_store(path: Path) -> None:
    conn = duckdb.connect(str(path))
    try:
        configure_connection(conn)
        init_schema(conn)
        load_universe_fixtures(conn, REPO / "tests" / "fixtures" / "universe")
    finally:
        conn.close()


def _snapshot(store: Path) -> dict[str, tuple[int, str]]:
    conn = duckdb.connect(str(store), read_only=True)
    try:
        return table_snapshot(conn)
    finally:
        conn.close()


def test_d_snapshot_catches_the_planted_write(tmp_path: Path) -> None:
    store = tmp_path / "store.duckdb"
    _fixture_store(store)
    before = _snapshot(store)
    assert before["listings"][0] > 0
    conn = duckdb.connect(str(store))
    try:
        conn.execute(PLANTED_WRITE)
    finally:
        conn.close()
    assert changed_tables(before, _snapshot(store)) == {"listings"}


def test_d_store_tables_cover_every_named_table(tmp_path: Path) -> None:
    store = tmp_path / "store.duckdb"
    _fixture_store(store)
    # A fresh store has no lab table (strategy-lab plan choice 2); the lab
    # migration (T113, #1195) lists them in `REGISTRY_TABLE_NAMES`.
    conn = duckdb.connect(str(store))
    try:
        apply_lab_schema(conn)
    finally:
        conn.close()
    named = set(
        TABLE_NAMES + JOURNAL_TABLE_NAMES + LATER_JOURNAL_TABLE_NAMES + MASTER_CHECK_TABLE_NAMES
    )
    assert named | set(REGISTRY_TABLE_NAMES) | set(RESEARCH_TABLE_NAMES) <= set(_snapshot(store))


def _register_two_row_frame(settings: Any) -> tuple[int, Any]:
    """Register the scenario's registrations, a one-row gold export with its baseline
    `dev` run (the drift probe a frame batch runs first, C5) and the two-row fixture
    frame on the fixture store; returns the frame's dataset id and the drift probe.
    Written by T123b, the second of T123 and T123b to land (T118's declared stub)."""
    from research.fake_model_client import (  # type: ignore[import-not-found]
        Answer,
        ScriptedModelClient,
    )
    from research.labeling.test_job import (  # type: ignore[import-not-found]
        MERGER,
        MODEL,
        _experiment,
        _gold_row,
        _row,
        _write,
    )

    from tradepartner.research.labeling import job
    from tradepartner.store import research

    workspace = Path(settings.store.path).parent
    start = datetime(2019, 1, 2, 15, tzinfo=UTC)
    frame_rows = [
        _row("F1", start, eightk=False),
        _row("F2", start + timedelta(days=1), eightk=False),
    ]
    gold_rows = [_gold_row("D1", datetime(2016, 3, 1, 15, tzinfo=UTC), MERGER)]
    conn = duckdb.connect(settings.store.path)
    try:
        for slug, kind, dataset, splits, metric, direction, threshold in (
            (
                "departure-reason-batches",
                "benchmark",
                "departure-reason-frame",
                ("full",),
                "yield",
                "greater",
                None,
            ),
            (
                "departure-reason-pilot",
                "benchmark",
                "departure-reason-gold",
                ("dev", "pilot"),
                "class_accuracy",
                "greater",
                0.80,
            ),
            (
                "departure-reason-drift",
                "robustness",
                "departure-reason-gold",
                ("dev",),
                "flip_rate",
                "less",
                0.10,
            ),
        ):
            parsed = _experiment(
                slug,
                kind=kind,
                dataset=dataset,
                splits=splits,
                metric=metric,
                direction=direction,
                threshold=threshold,
            )
            research.register_experiment(conn, parsed, "owner")
        gold_path, gold_sha = _write(workspace, "gold", gold_rows)
        split_file = workspace / "gold.splits.json"
        split_file.write_text(json.dumps({"splits": ["dev"]}))
        day = gold_rows[0]["form25_accepted_at"].date()
        gold_id = research.register_dataset(
            conn,
            name="departure-reason-gold",
            version=gold_sha[:12],
            path=str(gold_path),
            sha256=gold_sha,
            event_start=day,
            event_end=day,
            n_rows=1,
            event_column="form25_accepted_at",
            split_path=str(split_file),
            split_sha256=sha256(split_file.read_bytes()).hexdigest(),
            split_spans={"dev": (day, day)},
            locked=True,
            seed=11,
        ).dataset_id
        baseline = job.run_batch(
            conn,
            settings,
            lambda _s, _h: ScriptedModelClient([Answer(MERGER)]),
            "departure-reason-pilot",
            gold_id,
            "dev",
            model=MODEL,
            sleep=lambda _s: None,
        )
        assert baseline.outcome == "ok", baseline
        frame_path, frame_sha = _write(workspace, "frame", frame_rows)
        days = [r["form25_accepted_at"].date() for r in frame_rows]
        frame_id = research.register_dataset(
            conn,
            name="departure-reason-frame",
            version=frame_sha[:12],
            path=str(frame_path),
            sha256=frame_sha,
            event_start=min(days),
            event_end=max(days),
            n_rows=2,
            event_column="form25_accepted_at",
        ).dataset_id
    finally:
        conn.close()
    return frame_id, job.DriftProbe(dataset_id=gold_id, baseline_run_id=baseline.run_id)


def _labeling_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, plant: bool
) -> tuple[set[str], dict[str, str], dict[str, str], str, str]:
    """Req 15's scenario: a fixture store with the research tables and a two-row
    fixture frame, `research.data_dir` in `tmp_path`, a labeling run through the
    scripted double (`job.run_batch`), the review session
    (`review.build_review_session`), `review.record_decision` for every item, and
    `review.finish` (C13). Returns the tables changed, the workspace's
    file hashes before and after (the events file among them), and the tracked-file
    status before and after.

    The calls follow the landed entry points (T123b edited them, as T118's stub
    allowed): `run_batch` returns a `BatchResult` and a frame batch runs the drift
    probe first, and the review takes the registry connection as `connect`. The
    assertions in the tests below are T118's and do not change."""
    from research.fake_model_client import (  # type: ignore[import-not-found]
        Answer,
        ScriptedModelClient,
    )
    from research.labeling.test_job import MERGER, MODEL  # type: ignore[import-not-found]

    from tradepartner.config import Settings
    from tradepartner.research.labeling import job, review

    store = tmp_path / "store.duckdb"
    data_dir = tmp_path / "research"
    events = tmp_path / "events.duckdb"  # stand-in until `events.path` exists in config
    _fixture_store(store)
    events.write_bytes(b"events stand-in")
    for name in (*_CEILING_ENV, KEY_NAME):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STORE__PATH", str(store))
    monkeypatch.setenv("RESEARCH__DATA_DIR", str(data_dir))
    # The scripted double spends nothing, but the job's spend check refuses every call
    # under the zero defaults (C4), so the scenario sets the two ceilings (T123b).
    for name in _CEILING_ENV:
        monkeypatch.setenv(name, "40")
    settings = Settings(_env_file=None)
    frame_id, drift = _register_two_row_frame(settings)

    before_tables = _snapshot(store)
    before_files = file_hashes(tmp_path, skip=(store, data_dir))
    before_git = tracked_changes()

    if plant:
        finish = review.finish

        def finish_with_planted_write(*args: Any, **kwargs: Any) -> Any:
            result = finish(*args, **kwargs)
            conn = duckdb.connect(str(store))
            try:
                conn.execute(PLANTED_WRITE)
            finally:
                conn.close()
            return result

        monkeypatch.setattr(review, "finish", finish_with_planted_write)

    drift_client = ScriptedModelClient([Answer(MERGER)])
    batch_client = ScriptedModelClient([Answer(MERGER), Answer("exchange_transfer")])

    def client_factory(_settings: object, handle: Any) -> Any:
        return drift_client if handle.slug == job.DRIFT_SLUG else batch_client

    @contextlib.contextmanager
    def connect() -> Iterator[duckdb.DuckDBPyConnection]:
        conn = duckdb.connect(str(store))
        try:
            yield conn
        finally:
            conn.close()

    with connect() as conn:
        batch = job.run_batch(
            conn,
            settings,
            client_factory,
            "departure-reason-batches",
            frame_id,
            "full",
            model=MODEL,
            drift=drift,
            sleep=lambda _s: None,
        )
    assert batch.outcome == "unfinished", batch
    session = review.build_review_session(
        batch.run_id, settings=settings, connect=connect, code_version="fixture"
    )
    assert len(session.items) == 2
    for item in session.items:
        review.record_decision(session, item, decision="a", reason="fixture", relied_on="notice")
    review.finish(session)

    return (
        changed_tables(before_tables, _snapshot(store)),
        before_files,
        file_hashes(tmp_path, skip=(store, data_dir)),
        before_git,
        tracked_changes(),
    )


def test_d_labeling_run_and_review_leave_the_store_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed, files_before, files_after, git_before, git_after = _labeling_scenario(
        tmp_path, monkeypatch, plant=False
    )
    assert changed <= RESEARCH_WRITES, changed - RESEARCH_WRITES
    assert "research_runs" in changed
    assert files_after == files_before
    assert git_after == git_before


def test_d_fails_on_a_planted_write_in_the_review_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed, *_ = _labeling_scenario(tmp_path, monkeypatch, plant=True)
    assert "listings" in changed - RESEARCH_WRITES


# --- (e) the zero default and the recordings (spec req 16 as C9 and C13 amend it) ------

_CEILING_ENV = ("RESEARCH__SPEND_CEILING_USD_MONTH", "RESEARCH__SPEND_CEILING_USD_TOTAL")
_KEY_ENV = KEY_NAME


def _models() -> Any:
    """T119 landed `research.models`, so (e) runs unconditionally (no skip)."""
    from tradepartner.research import models

    return models


def _settings(monkeypatch: pytest.MonkeyPatch, env: Mapping[str, str]) -> Any:
    from tradepartner.config import Settings

    for name in (*_CEILING_ENV, _KEY_ENV):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def _handle() -> Any:
    from datetime import UTC, datetime

    from tradepartner.research import _issue_run_handle

    return _issue_run_handle(
        run_id=1,
        registration_id=1,
        slug="departure-reason-batches",
        kind="benchmark",
        family=None,
        touches_returns=False,
        dataset=None,
        split="full",
        n_configurations_declared=1,
        synthetic=True,
        known_at=datetime(2026, 10, 6, tzinfo=UTC),
        store_max_ingested_at=None,
        database=None,
        refusal=None,
        message=None,
    )


def _refuse(what: str) -> Callable[..., NoReturn]:
    def refuse(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail(f"{what} was called")

    return refuse


def _refuse_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every httpx send path fails the test, whatever the import style or order
    (`httpx.post` and a `from httpx import Client` both end in a transport)."""
    import httpx

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _refuse("an httpx send"))
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", _refuse("an httpx async send")
    )


def _refuse_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """`httpx.Client` (spec req 16) and `httpx.AsyncClient` fail if constructed."""
    import httpx

    monkeypatch.setattr(httpx, "Client", _refuse("httpx.Client"))
    monkeypatch.setattr(httpx, "AsyncClient", _refuse("httpx.AsyncClient"))


@pytest.fixture
def no_httpx_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """No httpx client is constructed and nothing is sent."""
    _refuse_clients(monkeypatch)
    _refuse_sends(monkeypatch)


def _public_methods(protocol: type) -> list[str]:
    return [n for n in dir(protocol) if not n.startswith("_") and callable(getattr(protocol, n))]


def test_e_the_defaults_are_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _models()
    settings = _settings(monkeypatch, {})
    assert settings.research.spend_ceiling_usd_month == 0.0
    assert settings.research.spend_ceiling_usd_total == 0.0
    assert settings.typesafe_api_key is None


@pytest.mark.usefixtures("no_httpx_client")
def test_e_default_settings_give_a_disabled_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspect

    models = _models()
    client = models.build_client(_settings(monkeypatch, {}), _handle())
    methods = _public_methods(models.ModelClient)
    assert methods, "ModelClient declares no call"
    for name in methods:
        call = getattr(client, name)
        params = inspect.signature(call).parameters.values()
        args = {p.name: None for p in params if p.default is inspect.Parameter.empty}
        with pytest.raises(models.ModelDisabled):
            call(**args)


@pytest.mark.usefixtures("no_httpx_client")
@pytest.mark.parametrize(
    "env",
    [
        {},
        {"RESEARCH__SPEND_CEILING_USD_MONTH": "10", "RESEARCH__SPEND_CEILING_USD_TOTAL": "20"},
        {
            "RESEARCH__SPEND_CEILING_USD_MONTH": "10",
            "RESEARCH__SPEND_CEILING_USD_TOTAL": "20",
            _KEY_ENV: "x",
        },
    ],
)
def test_e_no_handle_raises_before_anything_else(
    monkeypatch: pytest.MonkeyPatch, env: Mapping[str, str]
) -> None:
    from tradepartner.research import NoRunHandle

    models = _models()
    with pytest.raises(NoRunHandle):
        models.build_client(_settings(monkeypatch, env), None)


@pytest.mark.parametrize(
    ("env", "real"),
    [
        ({"RESEARCH__SPEND_CEILING_USD_MONTH": "10", _KEY_ENV: "x"}, False),
        ({"RESEARCH__SPEND_CEILING_USD_TOTAL": "20", _KEY_ENV: "x"}, False),
        (
            {"RESEARCH__SPEND_CEILING_USD_MONTH": "10", "RESEARCH__SPEND_CEILING_USD_TOTAL": "20"},
            False,
        ),
        (
            {
                "RESEARCH__SPEND_CEILING_USD_MONTH": "10",
                "RESEARCH__SPEND_CEILING_USD_TOTAL": "20",
                _KEY_ENV: "x",
            },
            True,
        ),
    ],
)
def test_e_the_real_client_needs_both_ceilings_and_a_key(
    monkeypatch: pytest.MonkeyPatch, env: Mapping[str, str], real: bool
) -> None:
    models = _models()
    _refuse_sends(monkeypatch)
    with monkeypatch.context() as m:
        _refuse_clients(m)
        disabled = type(models.build_client(_settings(monkeypatch, {}), _handle()))
    if not real:
        _refuse_clients(monkeypatch)
    client = models.build_client(_settings(monkeypatch, env), _handle())
    assert (type(client) is not disabled) is real


#: A versioned model id; `jev-latest` and `jev-preview` are aliases (spec req 7).
_VERSIONED_MODEL = re.compile(r"^[a-z][a-z0-9-]*-\d+\.\d+\.\d+$")


def recording_problems(name: str, recording: object) -> list[str]:
    """The contract a recorded response must meet (spec C9, the shape of E8)."""
    if not isinstance(recording, dict):
        return [f"{name}: not a JSON object"]
    problems: list[str] = []
    model = recording.get("model")
    if not isinstance(model, str) or not _VERSIONED_MODEL.match(model):
        problems.append(f"{name}: model {model!r} is not a versioned id (an alias?)")
    answers = recording.get("answers")
    if not isinstance(answers, dict) or not answers:
        problems.append(f"{name}: no answers")
    else:
        for question, answer in answers.items():
            if not isinstance(answer, dict) or not {"type", "choice", "probabilities"} <= set(
                answer
            ):
                problems.append(f"{name}: answer {question!r} lacks type, choice or probabilities")
    usage = recording.get("usage")
    if not isinstance(usage, dict) or not isinstance(usage.get("input_tokens"), int):
        problems.append(f"{name}: no usage.input_tokens")
    return problems


def _recordings() -> list[Path]:
    return sorted(TYPESAFE_RECORDINGS.glob("*.json")) if TYPESAFE_RECORDINGS.is_dir() else []


def test_e_contract_over_the_owner_recordings() -> None:
    recordings = _recordings()
    if not recordings:
        pytest.skip(RECORDINGS_PENDING)
    problems = [
        problem
        for path in recordings
        for problem in recording_problems(path.stem, json.loads(path.read_text(encoding="utf-8")))
    ]
    assert problems == []


_GOOD_RECORDING: dict[str, Any] = {
    "model": "jev-1.13.0",
    "answers": {
        "departure_reason": {
            "type": "choice",
            "choice": "bankruptcy",
            "probabilities": {"bankruptcy": 1.0},
            "confidence": 1.0,
        }
    },
    "usage": {"input_tokens": 1200, "output_tokens": 3},
}


def test_e_contract_accepts_a_versioned_recording() -> None:
    assert recording_problems("bankruptcy", _GOOD_RECORDING) == []


@pytest.mark.parametrize("alias", ["jev-latest", "jev-preview"])
def test_e_an_alias_in_a_recording_fails(alias: str) -> None:
    assert recording_problems("bankruptcy", {**_GOOD_RECORDING, "model": alias})

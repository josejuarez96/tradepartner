"""#3658: tests that patch or import a private name of the code under test
break on a rename that changes no behaviour. The private `.setattr(...)`
patches and private `from tradepartner... import _name` imports that exist
today are a per-file baseline that may only shrink.

Regenerate `tests/fixtures/private_coupling_baseline.json` after removing a
patch or an import: `uv run python tests/test_private_coupling.py`."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_BASELINE = _TESTS / "fixtures" / "private_coupling_baseline.json"
_PACKAGE = "tradepartner"
_KINDS = ("patches", "imports")
_REGENERATE = "uv run python tests/test_private_coupling.py"
_ADVICE = (
    "test through the public function or an injected argument; raise the baseline only with "
    "a reason in the PR"
)

Counts = dict[str, tuple[int, int]]


def _is_private(name: str) -> bool:
    """A leading underscore, dunders (`__x__`) excepted."""
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def _string(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_private_patch(call: ast.Call) -> bool:
    """`<anything>.setattr(obj, "_name", ...)` or `<anything>.setattr("pkg.mod._name", ...)`;
    the builtin `setattr(obj, name, value)` is not a patch."""
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "setattr" and call.args):
        return False
    target = _string(call.args[0])
    if target is not None:
        return _is_private(target.rsplit(".", 1)[-1])
    name = _string(call.args[1]) if len(call.args) > 1 else None
    return name is not None and _is_private(name)


def scan(source: str) -> tuple[int, int]:
    """`(private_patches, private_imports)` of one test source: `.setattr`
    calls that target a private name, and private names imported absolutely
    from `tradepartner` or a module below it."""
    patches = imports = 0
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            patches += _is_private_patch(node)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            if module == _PACKAGE or module.startswith(_PACKAGE + "."):
                imports += sum(_is_private(alias.name) for alias in node.names)
    return patches, imports


def _scan_tree() -> Counts:
    """The scan of every `.py` file under `tests/`, keyed by the posix path
    relative to it; files with neither count are left out."""
    counts: Counts = {}
    for path in sorted(_TESTS.rglob("*.py")):
        found = scan(path.read_text(encoding="utf-8"))
        if found != (0, 0):
            counts[path.relative_to(_TESTS).as_posix()] = found
    return counts


def _render(counts: Counts) -> str:
    """The baseline file's text: sorted keys, one file per line."""
    lines = [
        f"{json.dumps(path)}: {json.dumps(dict(zip(_KINDS, found, strict=True)))}"
        for path, found in sorted(counts.items())
    ]
    return "{\n" + ",\n".join(lines) + "\n}\n"


def _baseline() -> Counts:
    raw: dict[str, dict[str, int]] = json.loads(_BASELINE.read_text(encoding="utf-8"))
    return {path: (entry["patches"], entry["imports"]) for path, entry in raw.items()}


def _grown(found: Counts, baseline: Counts) -> list[tuple[str, str, int, int]]:
    """`(file, kind, baseline, found)` for each count above its baseline; a
    file with no baseline entry is allowed none."""
    grown: list[tuple[str, str, int, int]] = []
    for path, counts in sorted(found.items()):
        allowed = baseline.get(path, (0, 0))
        grown.extend(
            (path, kind, was, now)
            for kind, was, now in zip(_KINDS, allowed, counts, strict=True)
            if now > was
        )
    return grown


def _stale(found: Counts, baseline: Counts, existing: set[str]) -> list[str]:
    """One line per baseline entry that names a missing file or differs from
    the scan."""
    problems: list[str] = []
    for path, allowed in sorted(baseline.items()):
        if path not in existing:
            problems.append(f"{path}: file does not exist")
            continue
        counts = found.get(path, (0, 0))
        problems.extend(
            f"{path}: {kind} baseline {was}, found {now}"
            for kind, was, now in zip(_KINDS, allowed, counts, strict=True)
            if now != was
        )
    return problems


@pytest.fixture(scope="module")
def found() -> Counts:
    return _scan_tree()


@pytest.fixture(scope="module")
def baseline() -> Counts:
    return _baseline()


def test_no_file_grows_its_private_coupling(found: Counts, baseline: Counts) -> None:
    grown = [
        f"tests/{path}: {kind} baseline {was}, found {now}"
        for path, kind, was, now in _grown(found, baseline)
    ]
    assert not grown, "more private coupling:\n  " + "\n  ".join(grown) + f"\n{_ADVICE}"


def test_the_baseline_is_not_stale(found: Counts, baseline: Counts) -> None:
    existing = {path for path in baseline if (_TESTS / path).is_file()}
    stale = _stale(found, baseline, existing)
    assert not stale, (
        "the baseline no longer equals the scan (paths under tests/):\n  "
        + "\n  ".join(stale)
        + f"\nif a patch or import was removed, lower the baseline in the same PR with "
        f"`{_REGENERATE}`; if one was added, {_ADVICE}"
    )


def test_the_baseline_file_is_sorted_and_minimal(baseline: Counts) -> None:
    assert list(baseline) == sorted(baseline), "baseline keys must be sorted"
    assert (0, 0) not in baseline.values(), "a file with no private coupling has no entry"
    assert _BASELINE.read_text(encoding="utf-8") == _render(baseline), "one file per line"


def test_scan_counts_a_planted_private_patch_and_import() -> None:
    source = (
        "from tradepartner.a import _helper, public\n"
        "from other import _q\n"
        "def test_x(monkeypatch, mp, mod, obj):\n"
        '    monkeypatch.setattr(mod, "_x", 1)\n'
        '    mp.setattr("tradepartner.a._y", 1)\n'
        '    monkeypatch.setattr(mod, "public", 1)\n'
        '    setattr(obj, "_z", 1)\n'
    )
    assert scan(source) == (2, 1)


def test_scan_edges() -> None:
    assert scan('with MonkeyPatch.context() as ctx:\n    ctx.setattr(mod, "_x", 1)\n') == (1, 0)
    assert scan('mp.setattr("tradepartner.a.public", "_value")\n') == (0, 0)
    assert scan('mp.setattr(mod, name, "_value")\nmp.setattr(mod._inner, "public", 1)\n') == (0, 0)
    assert scan('mp.setitem(d, "_k", 1)\nmp.delattr(mod, "_x")\n') == (0, 0)
    assert scan('"""monkeypatch.setattr(mod, "_x", 1)"""\n') == (0, 0)
    assert scan("from tradepartner import _a\nimport tradepartner._b\n") == (0, 1)
    assert scan("from tradepartnerx import _a\nfrom .tradepartner import _b\n") == (0, 0)
    assert scan("from tradepartner.a import _one as one, _two\n") == (0, 2)


def test_a_dunder_is_not_private() -> None:
    assert _is_private("_x")
    assert _is_private("__x")
    assert not _is_private("__x__")
    assert not _is_private("public")
    source = (
        "from tradepartner import __version__\n"
        'monkeypatch.setattr(mod, "__call__", f)\n'
        'monkeypatch.setattr("tradepartner.a.__all__", [])\n'
    )
    assert scan(source) == (0, 0)


def test_growth_is_reported() -> None:
    found = {"a.py": (3, 1), "new.py": (0, 1), "same.py": (2, 2), "lower.py": (1, 0)}
    baseline = {"a.py": (2, 1), "same.py": (2, 2), "lower.py": (2, 0), "gone.py": (1, 1)}
    assert _grown(found, baseline) == [("a.py", "patches", 2, 3), ("new.py", "imports", 0, 1)]
    assert _stale(found, baseline, {"a.py", "same.py", "lower.py"}) == [
        "a.py: patches baseline 2, found 3",
        "gone.py: file does not exist",
        "lower.py: patches baseline 2, found 1",
    ]


def test_the_scan_sees_the_tree(found: Counts) -> None:
    assert found["test_ingest.py"][0] >= 20
    assert found["adapters/test_edgar_source.py"][0] >= 20
    assert sum(imports for _, imports in found.values()) >= 50


if __name__ == "__main__":
    _BASELINE.write_text(_render(_scan_tree()), encoding="utf-8")

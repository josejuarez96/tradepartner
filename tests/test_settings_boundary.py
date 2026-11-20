"""#3645: configuration enters at the CLI. Below it, a function takes
`settings: Settings` as a parameter; it does not call `get_settings()`. The
calls that exist today are a per-file baseline that may only shrink.

Regenerate `tests/fixtures/get_settings_baseline.txt` after removing a call:
`uv run python tests/test_settings_boundary.py`."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src" / "tradepartner"
_BASELINE = _REPO / "tests" / "fixtures" / "get_settings_baseline.txt"
# Where configuration is meant to enter; `config.py` defines the function.
_ENTRY_FILES = frozenset({"cli.py", "cli_record.py"})
_ENTRY_DIRS = frozenset({"dashboard"})
_ADVICE = "take `settings: Settings` as a parameter and let the CLI pass it"


def _calls(source: str) -> int:
    """The number of `get_settings(...)` calls in `source`, by name or as an
    attribute (`config.get_settings()`); text in strings does not count."""
    count = 0
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Name) and func.id == "get_settings") or (
            isinstance(func, ast.Attribute) and func.attr == "get_settings"
        ):
            count += 1
    return count


def _scan() -> dict[str, int]:
    """`get_settings()` calls per file below the CLI, keyed by the path
    relative to the repository root; files with none are left out."""
    counts: dict[str, int] = {}
    for path in sorted(_SRC.rglob("*.py")):
        relative = path.relative_to(_SRC)
        if relative.as_posix() in _ENTRY_FILES or relative.parts[0] in _ENTRY_DIRS:
            continue
        count = _calls(path.read_text(encoding="utf-8"))
        if count:
            counts[path.relative_to(_REPO).as_posix()] = count
    return counts


def _render(counts: dict[str, int]) -> str:
    return "".join(f"{count} {path}\n" for path, count in sorted(counts.items()))


def _baseline() -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in _BASELINE.read_text(encoding="utf-8").splitlines():
        count, path = line.split(" ", 1)
        counts[path] = int(count)
    return counts


def test_no_module_below_the_cli_gains_a_get_settings_call() -> None:
    baseline = _baseline()
    grown = {
        path: (baseline.get(path, 0), count)
        for path, count in _scan().items()
        if count > baseline.get(path, 0)
    }
    assert not grown, f"new get_settings() calls (baseline, now): {grown}; {_ADVICE}"


def test_the_baseline_is_not_stale() -> None:
    assert _render(_scan()) == _BASELINE.read_text(encoding="utf-8"), (
        "the get_settings() counts changed; if a call was removed, regenerate the baseline "
        f"with `uv run python tests/test_settings_boundary.py`; if one was added, {_ADVICE}"
    )


def test_the_counter_on_planted_sources() -> None:
    assert _calls("x = get_settings()\n") == 1
    assert _calls("x = config.get_settings()\n") == 1
    assert _calls("settings = settings or get_settings()\n") == 1
    assert _calls('def f() -> None:\n    """Defaults to `get_settings()`."""\n') == 0
    assert _calls("loader = get_settings\nrun(get_settings)\n") == 0


def test_the_scan_sees_the_tree() -> None:
    counts = _scan()
    assert counts["src/tradepartner/adapters/edgar_raw.py"] >= 20
    assert not any(path.endswith(("/cli.py", "/cli_record.py")) for path in counts)
    assert not any("/dashboard/" in path for path in counts)


if __name__ == "__main__":
    _BASELINE.write_text(_render(_scan()), encoding="utf-8")

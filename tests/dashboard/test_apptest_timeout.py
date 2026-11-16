"""Every dashboard `AppTest` gets the shared generous timeout (#3197).

Streamlit's `AppTest` fails a script run after 3 s by default. The dashboard pages
render in 0.5-0.9 s locally, but loaded CI shards crossed 3 s on correct pages. So
every `AppTest` in `tests/dashboard` is built through `dashboard_nav.app_test` (or
passes `default_timeout=APPTEST_TIMEOUT_S`), and no `.run()` narrows it with its own
`timeout=`. This test reads the test sources and fails on a site that does not.
"""

from __future__ import annotations

import ast
from pathlib import Path

from dashboard_nav import APPTEST_TIMEOUT_S

_DIR = Path(__file__).parent
_BUILDERS = {"from_file", "from_function", "from_string"}


def _calls() -> list[tuple[str, ast.Call]]:
    """Every call in the other dashboard test modules, with its `file:line`."""
    found: list[tuple[str, ast.Call]] = []
    for path in sorted(_DIR.glob("*.py")):
        if path.name == Path(__file__).name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found += [
            (f"{path.name}:{node.lineno}", node)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        ]
    return found


def _is_build(call: ast.Call) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in _BUILDERS
        and isinstance(func.value, ast.Name)
        and func.value.id == "AppTest"
    )


def _uses_shared_timeout(call: ast.Call) -> bool:
    return any(
        kw.arg == "default_timeout"
        and isinstance(kw.value, ast.Name)
        and kw.value.id == "APPTEST_TIMEOUT_S"
        for kw in call.keywords
    )


def test_timeout_is_generous() -> None:
    """The shared timeout leaves CI load far above a 0.9 s local render."""
    assert APPTEST_TIMEOUT_S >= 30


def test_guard_sees_the_builds() -> None:
    """The scan finds `AppTest` builds at all, so the checks below cannot pass empty."""
    assert any(_is_build(call) for _, call in _calls())


def test_every_apptest_build_uses_the_shared_timeout() -> None:
    """Each direct `AppTest.from_*` call passes `default_timeout=APPTEST_TIMEOUT_S`."""
    offenders = [
        where for where, call in _calls() if _is_build(call) and not _uses_shared_timeout(call)
    ]
    assert offenders == []


def test_no_run_narrows_the_timeout() -> None:
    """No `.run(timeout=...)` overrides the shared default with its own value."""
    offenders = [
        where
        for where, call in _calls()
        if isinstance(call.func, ast.Attribute)
        and call.func.attr == "run"
        and any(kw.arg == "timeout" for kw in call.keywords)
    ]
    assert offenders == []

"""#3636: SQL belongs in `src/tradepartner/store/`, where the point-in-time
rules of `store/asof.py` live. The `execute` / `executemany` call sites that
exist outside it today are a per-file baseline that may only shrink.

Regenerate `tests/fixtures/sql_boundary_baseline.txt` after removing a site, or
after moving sites between files without raising the total:
`uv run python tests/test_sql_boundary.py`."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src" / "tradepartner"
_BASELINE = _REPO / "tests" / "fixtures" / "sql_boundary_baseline.txt"
_STORE = "store/"
_METHODS = frozenset({"execute", "executemany"})
_REGENERATE = "uv run python tests/test_sql_boundary.py"
_ADVICE = (
    "put the query in a function under src/tradepartner/store/ and call it; or, for a move "
    "between files, edit the baseline in the same PR without raising the total"
)
_HEADER = (
    "# #3636: `.execute(...)` / `.executemany(...)` call sites per file under src/tradepartner/\n"
    "# outside store/, counted by tests/test_sql_boundary.py. One line per file: <count> <path>,\n"
    "# the path relative to src/tradepartner/. The numbers only go down: a new query goes in a\n"
    "# function under src/tradepartner/store/. To lower a line after removing a site (or to move\n"
    f"# sites between files without raising the total), run `{_REGENERATE}`\n"
    "# and commit the result in the same PR.\n"
)


def _sites(source: str) -> int:
    """The number of calls in `source` to an attribute named `execute` or
    `executemany`, whatever the receiver; text in strings does not count."""
    return sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _METHODS
        for node in ast.walk(ast.parse(source))
    )


def _scan() -> dict[str, int]:
    """Execute sites per file under `src/tradepartner/` outside `store/`,
    keyed by the posix path relative to that directory; files with none are
    left out."""
    counts: dict[str, int] = {}
    for path in sorted(_SRC.rglob("*.py")):
        relative = path.relative_to(_SRC).as_posix()
        if relative.startswith(_STORE):
            continue
        count = _sites(path.read_text(encoding="utf-8"))
        if count:
            counts[relative] = count
    return counts


def _render(counts: dict[str, int]) -> str:
    return _HEADER + "".join(f"{count} {path}\n" for path, count in sorted(counts.items()))


def _baseline_lines() -> list[tuple[str, int]]:
    """The baseline's `(path, count)` lines in file order, comments skipped."""
    lines: list[tuple[str, int]] = []
    for line in _BASELINE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        count, path = line.split(" ", 1)
        lines.append((path, int(count)))
    return lines


def _grown(scan: dict[str, int], baseline: dict[str, int]) -> list[str]:
    """One line per file whose count is above its baseline (zero when the
    file has no baseline line)."""
    return [
        f"{path}: baseline {baseline.get(path, 0)}, found {count}"
        for path, count in sorted(scan.items())
        if count > baseline.get(path, 0)
    ]


def _stale(scan: dict[str, int], baseline: dict[str, int], existing: set[str]) -> list[str]:
    """One line per baseline entry that names a missing file or differs from
    the scan."""
    problems: list[str] = []
    for path, count in sorted(baseline.items()):
        if path not in existing:
            problems.append(f"{path}: baseline {count}, file does not exist")
        elif scan.get(path, 0) != count:
            problems.append(f"{path}: baseline {count}, found {scan.get(path, 0)}")
    return problems


@pytest.fixture(scope="module")
def scan() -> dict[str, int]:
    return _scan()


@pytest.fixture(scope="module")
def baseline() -> dict[str, int]:
    return dict(_baseline_lines())


def test_no_file_outside_store_gains_an_execute_site(
    scan: dict[str, int], baseline: dict[str, int]
) -> None:
    grown = _grown(scan, baseline)
    assert not grown, "new execute sites outside store/:\n  " + "\n  ".join(grown) + f"\n{_ADVICE}"


def test_the_total_never_rises(scan: dict[str, int], baseline: dict[str, int]) -> None:
    found, allowed = sum(scan.values()), sum(baseline.values())
    assert found <= allowed, (
        f"execute sites outside store/: baseline total {allowed}, found {found}; {_ADVICE}"
    )


def test_the_baseline_is_not_stale(scan: dict[str, int], baseline: dict[str, int]) -> None:
    existing = {path for path in baseline if (_SRC / path).is_file()}
    stale = _stale(scan, baseline, existing)
    assert not stale, (
        "the baseline no longer equals the scan:\n  "
        + "\n  ".join(stale)
        + f"\nif a site was removed, lower the baseline in the same PR with `{_REGENERATE}`; "
        f"if one was added, {_ADVICE}"
    )


def test_the_baseline_file_is_sorted_and_minimal() -> None:
    lines = _baseline_lines()
    paths = [path for path, _ in lines]
    assert paths == sorted(set(paths)), "baseline paths must be sorted and unique"
    assert all(count > 0 for _, count in lines), "a file with no site has no baseline line"
    assert not any(path.startswith(_STORE) for path in paths), "store/ is not ratcheted"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('conn.execute("SELECT 1")', 1),
        ("conn.executemany(q, rows)", 1),
        ("self._conn.execute(q).fetchall()", 1),
        ("executor.submit(f)", 0),
        ("class A:\n    def execute(self): ...", 0),
        ('def f():\n    """Calls `conn.execute(` once."""', 0),
        ('text = ".execute("', 0),
        ("def outer():\n    def inner():\n        return conn.execute(q)\n    return inner", 1),
        ("run = lambda conn: conn.execute(q)", 1),
        ("conn.execute(a)\ncur.executemany(b, rows)\nexecute(c)", 2),
    ],
)
def test_the_counter_on_planted_sources(source: str, expected: int) -> None:
    assert _sites(source) == expected


def test_growth_and_staleness_are_reported_on_planted_counts() -> None:
    baseline = {"a.py": 2, "gone.py": 1, "lower.py": 3}
    scan = {"a.py": 3, "lower.py": 2, "new.py": 1}
    assert _grown(scan, baseline) == ["a.py: baseline 2, found 3", "new.py: baseline 0, found 1"]
    assert _stale(scan, baseline, {"a.py", "lower.py"}) == [
        "a.py: baseline 2, found 3",
        "gone.py: baseline 1, file does not exist",
        "lower.py: baseline 3, found 2",
    ]


def test_the_scan_sees_the_tree(scan: dict[str, int]) -> None:
    for path in ("ingest.py", "health.py", "repair.py"):
        assert scan.get(path, 0) > 30, path
    assert not any(path.startswith(_STORE) for path in scan)


if __name__ == "__main__":
    _BASELINE.write_text(_render(_scan()), encoding="utf-8")

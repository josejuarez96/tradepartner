"""scripts/coverage_by_package.py (#3650): per-package rows from a `coverage json` report."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "coverage_by_package.py"
_spec = importlib.util.spec_from_file_location("coverage_by_package", _PATH)
assert _spec is not None and _spec.loader is not None
coverage_by_package = importlib.util.module_from_spec(_spec)
sys.modules["coverage_by_package"] = coverage_by_package
_spec.loader.exec_module(coverage_by_package)


def _file(statements: int, missing: int, branches: int, partial: int, covered_b: int) -> Any:
    return {
        "summary": {
            "num_statements": statements,
            "missing_lines": missing,
            "covered_lines": statements - missing,
            "num_branches": branches,
            "num_partial_branches": partial,
            "covered_branches": covered_b,
            "missing_branches": branches - covered_b,
        }
    }


_REPORT: dict[str, Any] = {
    "meta": {"branch_coverage": True},
    "files": {
        "src/tradepartner/store/db.py": _file(100, 10, 20, 2, 15),
        "src/tradepartner/store/schema.py": _file(50, 0, 10, 0, 10),
        "src/tradepartner/execution/risk.py": _file(40, 20, 10, 4, 5),
        "src/tradepartner/cli.py": _file(10, 10, 0, 0, 0),
    },
}


def test_packages_are_summed_from_file_totals() -> None:
    rows = coverage_by_package.by_package(_REPORT)
    assert [row.package for row in rows] == ["(top level)", "execution", "store"]
    by_name = {row.package: row for row in rows}
    store = by_name["store"]
    assert (store.statements, store.missing, store.branches, store.partial_branches) == (
        150,
        10,
        30,
        2,
    )
    # (140 covered lines + 25 covered branches) / (150 statements + 30 branches)
    assert store.percent == pytest.approx(100 * 165 / 180)
    assert by_name["execution"].percent == pytest.approx(100 * 25 / 50)
    assert by_name["(top level)"].percent == 0.0

    table = coverage_by_package.render(rows).splitlines()
    assert table[0].startswith("| Package |")
    assert [line.split("|")[1].strip() for line in table[2:]] == [
        "(top level)",
        "execution",
        "store",
        "**Total**",
    ]
    # Total: 200 statements, 40 missing, 40 branches, 30 covered branches.
    assert table[-1] == "| **Total** | 200 | 40 | 40 | 6 | 79.2% |"


def test_an_empty_report_gives_a_header_only() -> None:
    assert coverage_by_package.by_package({"files": {}}) == []
    assert coverage_by_package.by_package({}) == []
    assert len(coverage_by_package.render([]).splitlines()) == 2


def test_main_prints_the_table_of_a_report_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(_REPORT), encoding="utf-8")
    assert coverage_by_package.main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "| store | 150 | 10 | 30 | 2 | 91.7% |" in out
    assert coverage_by_package.main([]) == 2

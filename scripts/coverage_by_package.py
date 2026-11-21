#!/usr/bin/env python3
"""Per-package coverage table from a ``coverage json`` report (#3650).

The weekly coverage workflow (``.github/workflows/coverage.yml``) runs this on the combined
report and appends the output to the job summary::

    uv run python scripts/coverage_by_package.py coverage.json >> "$GITHUB_STEP_SUMMARY"

A package is the first directory under ``src/tradepartner/``; modules directly in it are
grouped as ``(top level)``. The percentage is coverage.py's own with branch measurement on:
covered lines plus covered branches, over statements plus branches. Standard library only.
There is no threshold: the numbers are for reading, nothing fails on a low one.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

_PACKAGE_DIR = "tradepartner"
_TOP_LEVEL = "(top level)"
_OUTSIDE = "(outside the package)"
_HEADER = (
    "| Package | Statements | Missed | Branches | Partial | Covered |\n"
    "|---|---:|---:|---:|---:|---:|"
)


@dataclass(frozen=True)
class PackageRow:
    """Summed coverage counts of one first-level package."""

    package: str
    statements: int
    missing: int
    branches: int
    partial_branches: int
    covered_branches: int

    @property
    def percent(self) -> float:
        """Covered lines plus covered branches, as a percentage of statements plus branches."""
        total = self.statements + self.branches
        if total == 0:
            return 100.0
        return 100.0 * (self.statements - self.missing + self.covered_branches) / total


def _package_of(path: str) -> str:
    """The first-level package a measured file belongs to."""
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if _PACKAGE_DIR not in parts:
        return _OUTSIDE
    below = parts[parts.index(_PACKAGE_DIR) + 1 :]
    return below[0] if len(below) > 1 else _TOP_LEVEL


def _sum(package: str, rows: Sequence[PackageRow]) -> PackageRow:
    """One row holding the summed counts of ``rows``."""
    return PackageRow(
        package=package,
        statements=sum(r.statements for r in rows),
        missing=sum(r.missing for r in rows),
        branches=sum(r.branches for r in rows),
        partial_branches=sum(r.partial_branches for r in rows),
        covered_branches=sum(r.covered_branches for r in rows),
    )


def by_package(report: Mapping[str, Any]) -> list[PackageRow]:
    """One row per first-level package of a ``coverage json`` report, sorted by package."""
    files: Mapping[str, Any] = report.get("files") or {}
    grouped: dict[str, list[PackageRow]] = {}
    for path, entry in files.items():
        summary: Mapping[str, Any] = entry["summary"]
        package = _package_of(path)
        grouped.setdefault(package, []).append(
            PackageRow(
                package=package,
                statements=int(summary["num_statements"]),
                missing=int(summary["missing_lines"]),
                branches=int(summary.get("num_branches", 0)),
                partial_branches=int(summary.get("num_partial_branches", 0)),
                covered_branches=int(summary.get("covered_branches", 0)),
            )
        )
    return [_sum(package, grouped[package]) for package in sorted(grouped)]


def _line(name: str, row: PackageRow) -> str:
    return (
        f"| {name} | {row.statements} | {row.missing} | {row.branches} "
        f"| {row.partial_branches} | {row.percent:.1f}% |"
    )


def render(rows: Sequence[PackageRow]) -> str:
    """The Markdown table of ``rows`` plus a total row; the header alone when there are none."""
    lines = [_HEADER]
    lines.extend(_line(row.package, row) for row in rows)
    if rows:
        lines.append(_line("**Total**", _sum("total", rows)))
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Print the per-package table of the ``coverage json`` file named in ``argv``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: coverage_by_package.py <coverage.json>", file=sys.stderr)
        return 2
    report = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    print(render(by_package(report)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

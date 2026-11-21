"""T444 (#3657): the report types of `health.py` live in `health_reports.py`.

`tradepartner.health` re-exports every moved name, so no caller's import
changes, and the new module never imports `tradepartner.health` (no cycle)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tradepartner import health, health_reports

#: The 42 names the slice moved: the report dataclasses and every module-level
#: name they load, transitively.
MOVED_NAMES = (
    "COVERAGE_REFERENCE_NAMES",
    "INDEX_MEMBERS_MISSING",
    "STORE_STALE",
    "FIGI_TYPES_DISAGREEMENTS",
    "FIGI_IDENTITY_DISAGREEMENTS",
    "IngestStatus",
    "Coverage",
    "BarGaps",
    "Unclassifiable",
    "StaticReliance",
    "DelistedNames",
    "ListedWithoutBars",
    "SplicedSeries",
    "_SPLICED_SCHEMA",
    "PriceJumps",
    "SharesOutliers",
    "SharesCapJumps",
    "AcceptedSameDayPairs",
    "StatementCoverage",
    "StatementFacts",
    "FilingEvents",
    "IncrementalEdgar",
    "Form4Compare",
    "RateSeriesHealth",
    "RatesHealth",
    "BorrowHealth",
    "MergersHealth",
    "DelistingReasonsHealth",
    "FigiTypes",
    "FigiIdentity",
    "IndexMembersCheck",
    "CoverageReference",
    "IntegrityCheck",
    "FilenameListings",
    "_DUPLICATE_DIVIDENDS_SCHEMA",
    "DuplicateDividends",
    "SuccessorChains",
    "HealthReport",
    "INGEST_TIME_ET",
    "_ET",
    "stale_sessions",
    "stale_gap_expected",
)


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_health_re_exports_every_moved_name(name: str) -> None:
    assert getattr(health, name) is getattr(health_reports, name)


def test_moved_names_are_the_whole_module() -> None:
    """Every class, function and constant `health_reports.py` defines is in the list."""
    tree = ast.parse(Path(health_reports.__file__).read_text(encoding="utf-8"))
    defined: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef):
            defined.append(node.name)
        elif isinstance(node, ast.Assign):
            defined.extend(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.append(node.target.id)
    assert tuple(defined) == MOVED_NAMES


def test_health_reports_does_not_import_health() -> None:
    tree = ast.parse(Path(health_reports.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    assert "tradepartner.health" not in imported

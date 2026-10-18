"""Tests for scripts/doc_check.py: the citation check and the warn-only size budgets (#1751).

The fixtures cite made-up IDs on purpose, so the repository scan skips this file
(doc-check: ignore-file).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("doc_check", ROOT / "scripts" / "doc_check.py")
assert _SPEC is not None and _SPEC.loader is not None
doc_check = importlib.util.module_from_spec(_SPEC)
sys.modules["doc_check"] = doc_check
_SPEC.loader.exec_module(doc_check)

PLAN = """# Plan: Widgets (Phase 2)

Spec: [widgets](../specs/widgets.md)

- [x] **T1: Base.** (#1, PR #2) · Files: `src/a.py` · Depends on: none
- [ ] **T2: Next.** Builds req 2 on T1 per ADR 0001; HO-1 holds. Depends on: T1
- [ ] **T3 (owner): Gate.** Per req 9, after T99, ADR 0042, HO-7 and backtest spec req 3.
"""
SPEC_WIDGETS = """# Spec: Widgets

## Requirements
1. **One.** Text.
2. **Two.** Text with a list:
   1. nested item, not a requirement
3. **Retired** (see #5).

## Open questions
9. Not a requirement.
"""
SPEC_BACKTEST = "# Spec: Backtest\n\n## Requirements\n1. **A.**\n2. **B.**\n"
CLAIMS = '[[claim]]\nid = "HO-1"\nstatement = "x"\n\n[[claim]]\nid = "ER-4"\nstatement = "y"\n'


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal repository with one plan, two specs, one ADR and two claims."""
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "specs").mkdir()
    (tmp_path / "docs" / "decisions").mkdir()
    (tmp_path / "docs" / "research").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs" / "plans" / "widgets.md").write_text(PLAN)
    (tmp_path / "docs" / "specs" / "widgets.md").write_text(SPEC_WIDGETS)
    (tmp_path / "docs" / "specs" / "backtest.md").write_text(SPEC_BACKTEST)
    (tmp_path / "docs" / "decisions" / "0001-first.md").write_text("# ADR 0001\n")
    (tmp_path / "docs" / "research" / "claims.toml").write_text(CLAIMS)
    return tmp_path


def _refs(findings: list[Any]) -> set[str]:
    return {f"{f.kind} {f.ref}" for f in findings}


def test_spec_requirements_reads_only_top_level_items_under_requirements() -> None:
    assert doc_check.spec_requirements(SPEC_WIDGETS) == frozenset({1, 2, 3})


def test_plan_tasks_reads_open_finished_and_owner_lines() -> None:
    assert doc_check.plan_tasks(PLAN) == {"T1", "T2", "T3"}


def test_a_split_tasks_former_id_resolves() -> None:
    split = "- [ ] **T7a: Pure half.** Split from T7.\n- [ ] **T7b: Engine half.** After T7a.\n"
    assert doc_check.plan_tasks(split) == {"T7", "T7a", "T7b"}


def test_open_plan_lines_flag_each_unresolved_kind(repo: Path) -> None:
    findings = doc_check.check_citations(repo)
    assert _refs(findings) == {
        "task T99",
        "ADR 0042",
        "claim HO-7",
        "requirement widgets req 9",
        "requirement backtest req 3",
    }
    assert {f.line for f in findings} == {7}


def test_finished_plan_lines_are_not_checked(repo: Path) -> None:
    plan = repo / "docs" / "plans" / "widgets.md"
    plan.write_text(PLAN + "- [x] **T4: Done.** cites T98 and ADR 0099\n")
    assert "task T98" not in _refs(doc_check.check_citations(repo))


def test_a_named_spec_beats_the_plans_own_spec(repo: Path) -> None:
    registry = doc_check.load_registry(repo)
    line = "see backtest spec req 2 and specs/widgets.md req 3"
    assert doc_check.check_line(line, "p", 1, registry, default_spec="nothing") == []
    bad = doc_check.check_line("backtest spec req 3", "p", 1, registry, default_spec="widgets")
    assert _refs(bad) == {"requirement backtest req 3"}


def test_source_files_resolve_req_against_any_spec(repo: Path) -> None:
    (repo / "src" / "mod.py").write_text('"""Implements req 3 (T2, ADR 0001, ER-4)."""\n')
    (repo / "tests" / "test_mod.py").write_text("# req 4 and T7 and ER-5\n")
    findings = [f for f in doc_check.check_citations(repo) if f.path.startswith(("src", "tests"))]
    assert {(f.path, f.kind, f.ref) for f in findings} == {
        ("tests/test_mod.py", "requirement", "req 4"),
        ("tests/test_mod.py", "task", "T7"),
        ("tests/test_mod.py", "claim", "ER-5"),
    }


def test_a_spec_named_in_another_clause_does_not_scope_the_req(repo: Path) -> None:
    registry = doc_check.load_registry(repo)
    line = "reuses the backtest spec's cost model for the widget path; implements req 3"
    assert doc_check.check_line(line, "p", 1, registry, default_spec="widgets") == []
    assert doc_check.check_line("per the Backtest spec req 3", "p", 1, registry) != []


def test_claim_ids_with_letters_are_checked(repo: Path) -> None:
    registry = doc_check.load_registry(repo)
    assert _refs(doc_check.check_line("see HO-P9 and HO-A", "p", 1, registry)) == {
        "claim HO-P9",
        "claim HO-A",
    }


def test_time_like_tokens_and_ignored_lines_are_not_citations(repo: Path) -> None:
    registry = doc_check.load_registry(repo)
    assert (
        doc_check.check_line("at T0 and 2026-01-02T04:00 and f'{d}T04:00'", "p", 1, registry) == []
    )
    assert doc_check.check_line('ids = ["T9a"]  # doc-check: ignore', "p", 1, registry) == []
    assert doc_check.check_line("HOT-1 and XHO-1 are no claim", "p", 1, registry) == []


def test_cite_exits_one_and_prints_each_finding(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert doc_check.main(["cite", "--root", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "docs/plans/widgets.md:7: task T99" in out


def test_cite_exits_zero_when_everything_resolves(repo: Path) -> None:
    plan = repo / "docs" / "plans" / "widgets.md"
    plan.write_text("\n".join(PLAN.splitlines()[:-1]) + "\n")
    assert doc_check.main(["cite", "--root", str(repo)]) == 0


def test_the_repository_citations_resolve() -> None:
    findings = doc_check.check_citations(ROOT)
    assert not findings, "unresolved citations:\n" + "\n".join(map(str, findings))


def test_size_warns_over_budget_and_always_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "big.md").write_text("x" * 2_500)
    (tmp_path / "small.md").write_text("x" * 999)
    budgets = {"big.md": 2.0, "small.md": 1.0, "missing.md": 1.0}
    assert doc_check.size_warnings(tmp_path, budgets) == [
        "WARN big.md: 2.5 KB, over its 2.0 KB budget"
    ]
    (tmp_path / "CLAUDE.md").write_text("x" * 9_000)
    assert doc_check.main(["size", "--root", str(tmp_path)]) == 0
    assert "WARN CLAUDE.md: 9.0 KB" in capsys.readouterr().out


def test_size_budgets_name_existing_files() -> None:
    missing = [rel for rel in doc_check.SIZE_BUDGETS_KB if not (ROOT / rel).exists()]
    assert not missing

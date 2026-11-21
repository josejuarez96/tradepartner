"""Shape guards for the weekly coverage run (#3650): scheduled or by hand, never a gate.

Plain text, like `tests/test_ci_workflow.py` (no YAML dependency).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOWS = _ROOT / ".github" / "workflows"
COVERAGE = _WORKFLOWS / "coverage.yml"
CI = _WORKFLOWS / "ci.yml"


def _shards(text: str) -> tuple[int, list[int]]:
    """`PYTEST_SHARD_COUNT` and the matrix shard list (the regexes of test_ci_workflow.py)."""
    count = re.search(r"PYTEST_SHARD_COUNT:\s*(\d+)", text)
    assert count, "no workflow-level PYTEST_SHARD_COUNT"
    matrix = re.search(r"shard:\s*\[([^\]]+)\]", text)
    assert matrix, "no shard matrix"
    return int(count.group(1)), [int(x) for x in matrix.group(1).split(",")]


def _top_level_block(text: str, key: str) -> list[str]:
    """The indented lines under a top-level `key:`."""
    lines = text.splitlines()
    start = lines.index(f"{key}:")
    block: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line.startswith(" "):
            break
        block.append(line)
    return block


def test_coverage_config_measures_branches_of_the_package() -> None:
    tool = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]
    run = tool["coverage"]["run"]
    assert run["branch"] is True
    assert run["source"] == ["src/tradepartner"]
    assert run["relative_files"] is True
    assert run["parallel"] is True
    # A plain `uv run pytest` and CI stay uninstrumented.
    assert "--cov" not in tool["pytest"]["ini_options"]["addopts"]


def test_the_coverage_workflow_never_runs_on_a_pr_or_a_push() -> None:
    text = COVERAGE.read_text(encoding="utf-8")
    triggers = [line.strip() for line in _top_level_block(text, "on")]
    assert "schedule:" in triggers
    assert "workflow_dispatch:" in triggers
    assert "pull_request" not in text
    assert "push:" not in text


def test_the_coverage_workflow_reports_no_required_check() -> None:
    """`checks` is the name the `protect-main` ruleset, `ready_pr.checks_state` and
    `merge_train._REQUIRED_CHECKS` read literally; this workflow must never report it."""
    text = COVERAGE.read_text(encoding="utf-8")
    job_keys = [
        m.group(1) for line in _top_level_block(text, "jobs") if (m := re.match(r"^  (\S+):", line))
    ]
    assert job_keys == ["coverage-shard", "coverage-report"]
    names = re.findall(r"^\s*name:\s*(.+?)\s*$", text, flags=re.MULTILINE)
    assert not [name for name in names if name.strip("'\"").lower().startswith("checks")], names


def test_the_coverage_shards_match_ci() -> None:
    coverage = _shards(COVERAGE.read_text(encoding="utf-8"))
    assert coverage == _shards(CI.read_text(encoding="utf-8"))
    count, indices = coverage
    assert indices == list(range(count))


def test_the_coverage_workflow_token_is_read_only() -> None:
    text = COVERAGE.read_text(encoding="utf-8")
    permissions = [line.strip() for line in text.splitlines() if "permissions:" in line]
    assert permissions == ["permissions:"], "one workflow-level permissions block, no job override"
    block = [line.strip() for line in _top_level_block(text, "permissions") if line.strip()]
    assert block == ["contents: read"]
    assert "write" not in text

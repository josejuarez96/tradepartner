"""CI workflow guards (#198): every merge to main gets a completed CI run."""

from __future__ import annotations

import re
from pathlib import Path

CI = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _concurrency_block() -> dict[str, str]:
    """Top-level `concurrency:` keys as raw strings (plain text; no YAML dependency)."""
    lines = CI.read_text().splitlines()
    start = lines.index("concurrency:")
    block: dict[str, str] = {}
    for line in lines[start + 1 :]:
        m = re.match(r"^  (\S[^:]*):\s*(.*)$", line)
        if not m:
            break
        block[m.group(1)] = m.group(2).strip()
    return block


MAIN_PER_COMMIT = "ci-${{ github.ref == 'refs/heads/main' && github.sha || github.ref }}"


def test_main_runs_are_never_cancelled() -> None:
    """On 2026-09-25 each push to main cancelled the run before it, so two broken merges
    went unseen (#189, #193). `cancel-in-progress: false` is not enough: a group keeps one
    running and one pending run, and a third push cancels the pending one. So main gets
    one group per commit, and no main run shares a group with another."""
    block = _concurrency_block()
    assert block["group"] == MAIN_PER_COMMIT, block
    assert block["cancel-in-progress"] == "${{ github.ref != 'refs/heads/main' }}", block


def test_pr_branches_still_cancel_superseded_runs() -> None:
    """Off main the group falls back to the ref, and a new push cancels the old run."""
    block = _concurrency_block()
    assert "|| github.ref }}" in block["group"], block
    assert "github.ref != 'refs/heads/main'" in block["cancel-in-progress"], block

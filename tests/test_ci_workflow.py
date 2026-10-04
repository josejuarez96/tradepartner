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


TRAIN = "startsWith(github.ref, 'refs/heads/train/')"
MAIN_PER_COMMIT = (
    "ci-${{ (github.ref == 'refs/heads/main' || " + TRAIN + ") && github.sha || github.ref }}"
)


def test_main_runs_are_never_cancelled() -> None:
    """On 2026-09-25 each push to main cancelled the run before it, so two broken merges
    went unseen (#189, #193). `cancel-in-progress: false` is not enough: a group keeps one
    running and one pending run, and a third push cancels the pending one. So main gets
    one group per commit, and no main run shares a group with another."""
    block = _concurrency_block()
    assert block["group"] == MAIN_PER_COMMIT, block
    assert block["cancel-in-progress"] == (
        "${{ github.ref != 'refs/heads/main' && !" + TRAIN + " }}"
    ), block


def test_pr_branches_still_cancel_superseded_runs() -> None:
    """Off main the group falls back to the ref, and a new push cancels the old run."""
    block = _concurrency_block()
    assert "|| github.ref }}" in block["group"], block
    assert "github.ref != 'refs/heads/main'" in block["cancel-in-progress"], block


def test_train_branches_run_on_push_per_commit_and_are_never_cancelled() -> None:
    """Merge-train spec req 4 (T73): a `train/**` push runs the full suite, in a group of
    its own commit, so a probe pushed during the batch run cancels nothing."""
    text = CI.read_text()
    assert "    branches: [main, 'train/**']" in text
    block = _concurrency_block()
    assert TRAIN in block["group"] and "&& github.sha" in block["group"], block
    assert "!" + TRAIN in block["cancel-in-progress"], block


def test_the_checks_job_token_is_read_only() -> None:
    """T73: the `checks` job runs a train batch's code on a push event, so its token only
    reads; the `claims` job stays PR-only."""
    text = CI.read_text()
    checks = text[text.index("  checks:\n") : text.index("  claims:\n")]
    assert "    permissions:\n      contents: read\n" in checks
    assert "write" not in checks
    claims = text[text.index("  claims:\n") :]
    assert "if: github.event_name == 'pull_request'" in claims


def test_tests_run_in_parallel_inside_the_single_checks_job() -> None:
    """#581: the suite runs with xdist inside the one `checks` job, so the check name the
    merge train and branch protection read does not change."""
    text = CI.read_text()
    assert "run: uv run pytest -n auto" in text
    assert "\n  checks:\n" in text


def test_uv_cache_is_keyed_on_the_lock_and_only_main_saves_it() -> None:
    """#582: restore everywhere, save only on main, so a train batch cannot poison it."""
    text = CI.read_text()
    assert "enable-cache: true" in text
    assert "cache-dependency-glob: uv.lock" in text
    assert "save-cache: ${{ github.ref == 'refs/heads/main' }}" in text
    assert "uv sync --locked" in text

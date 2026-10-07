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
    """T73: every job that can run a train batch's code on a push event (`checks-fast`,
    `pytest-shard`, and the `checks` aggregator — #1112 split the old single `checks`
    job into these) only reads; the `claims` job stays PR-only.

    Sliced from `checks-fast` (not `checks`): with the #1112 split, the literal
    substring `"  checks:\n"` matches the thin aggregator job first, which has no
    checkout step and runs no repository code — asserting read-only permissions on it
    alone would miss a `write` added to `checks-fast` or `pytest-shard`, the jobs that
    actually execute the diff (code-review finding on PR #1113)."""
    text = CI.read_text()
    jobs = text[text.index("  checks-fast:\n") : text.index("  claims:\n")]
    assert jobs.count("    permissions:\n      contents: read\n") >= 3  # fast, shard, checks
    assert "write" not in jobs
    claims = text[text.index("  claims:\n") :]
    assert "if: github.event_name == 'pull_request'" in claims


def test_tests_run_in_parallel_inside_each_shard() -> None:
    """#581: each shard job still runs its slice with xdist."""
    text = CI.read_text()
    assert "run: uv run pytest -n auto" in text


def test_checks_is_a_thin_aggregator_over_fast_and_shards() -> None:
    """#1112: pytest moved into a `pytest-shard` matrix for wall-clock, but the required
    check name every piece of merge tooling reads literally (the `protect-main` ruleset,
    `ready_pr.checks_state`, `merge_train._REQUIRED_CHECKS`) must stay exactly `checks`, so
    a final job by that name needs both the fast job and the shard matrix."""
    text = CI.read_text()
    assert "\n  checks-fast:\n" in text
    assert "\n  pytest-shard:\n" in text
    assert "\n  checks:\n" in text
    checks = text[text.index("\n  checks:\n") :].split("\n  claims:\n", 1)[0]
    lines = [ln.strip() for ln in checks.splitlines()]
    assert "needs: [checks-fast, pytest-shard]" in lines, lines
    assert "if: always()" in checks


def _job(name: str, nxt: str) -> str:
    text = CI.read_text()
    return text[text.index(f"\n  {name}:\n") : text.index(f"\n  {nxt}:\n")]


DRAFT = (
    "github.event.pull_request.draft == true && "
    "!contains(github.event.pull_request.labels.*.name, 'ci:full')"
)
DRAFT_NEEDING_TESTS = DRAFT + " && needs.checks-fast.outputs.tests-needed != 'no'"


def test_a_draft_pr_runs_no_shard_and_never_reports_the_required_checks() -> None:
    """#1192: a draft without `ci:full` runs `checks-fast` only. Its aggregator must then
    report under a name other than the required `checks`, so such a run can never satisfy
    it; every other run (non-draft or labelled PR, push) reports `checks` as before."""
    shard = _job("pytest-shard", "checks")
    assert f"if: needs.checks-fast.outputs.tests-needed != 'no' && !({DRAFT})" in shard
    checks = _job("checks", "claims")
    assert (
        "name: ${{ (" + DRAFT_NEEDING_TESTS + ") && 'checks (draft, no shards)' || 'checks' }}"
    ) in checks
    # the step's draft branch tests the same condition, so the name and the exit agree
    assert 'if [ "${{ ' + DRAFT_NEEDING_TESTS + ' }}" = "true" ]; then' in checks


def test_a_label_starts_the_full_run_and_ready_for_review_starts_none() -> None:
    """#1192: ready_pr labels a draft `ci:full` to get the full run as a pull_request run
    (a dispatched run never shows on the PR); marking ready must not start a second full
    run of the same head."""
    text = CI.read_text()
    on = text[text.index("\non:\n") : text.index("\nconcurrency:\n")]
    assert "    types: [opened, synchronize, reopened, labeled]\n" in on
    assert "ready_for_review]" not in on and "workflow_dispatch:" not in on


def test_only_a_push_to_main_may_skip_the_shards_on_a_tested_tree() -> None:
    """#1192: main skips the shards only on the exact word `skip`; a failed lookup reads as
    `run`. The lookup sits in the push-to-main branch alone, not on PRs or `train/**`."""
    fast = _job("checks-fast", "pytest-shard")
    assert (
        'elif [ "${{ github.event_name }}" = "push" ] && '
        '[ "${{ github.ref }}" = "refs/heads/main" ]; then\n'
        '            decision=$(python3 scripts/ci_tested_tree.py "$GITHUB_SHA") '
        "|| decision=run\n"
        '            if [ "$decision" = "skip" ]; then\n'
    ) in fast
    assert fast.count('ci_tested_tree.py "') == 1


def test_pytest_shard_matrix_sets_the_index_and_count_env() -> None:
    """Each shard tells `tests/conftest.py`'s bucketing hook which slice it is."""
    text = CI.read_text()
    shard = text[text.index("\n  pytest-shard:\n") : text.index("\n  checks:\n")]
    assert "matrix:\n        shard: [0, 1, 2, 3, 4, 5, 6, 7]" in shard
    assert "PYTEST_SHARD_INDEX: ${{ matrix.shard }}" in shard
    assert "fail-fast: false" in shard


def test_shard_count_matches_the_matrix_length() -> None:
    """The workflow-level `PYTEST_SHARD_COUNT` and the matrix's shard list must agree, or
    some shard index would never be requested (or some would be requested but unassigned)."""
    text = CI.read_text()
    m = re.search(r"PYTEST_SHARD_COUNT:\s*(\d+)", text)
    assert m, "no workflow-level PYTEST_SHARD_COUNT"
    count = int(m.group(1))
    m = re.search(r"shard:\s*\[([^\]]+)\]", text)
    assert m, "no shard matrix"
    indices = [int(x) for x in m.group(1).split(",")]
    assert indices == list(range(count)), indices


def test_pytest_shard_skips_with_checks_fast_when_no_tests_needed() -> None:
    """A docs-only PR must skip every shard too, not just the fast job's own Tests step."""
    text = CI.read_text()
    shard = text[text.index("\n  pytest-shard:\n") : text.index("\n  checks:\n")]
    assert "if: needs.checks-fast.outputs.tests-needed != 'no'" in shard


def test_uv_cache_is_keyed_on_the_lock_and_only_main_saves_it() -> None:
    """#582: restore everywhere, save only on main, so a train batch cannot poison it."""
    text = CI.read_text()
    assert "enable-cache: true" in text
    assert "cache-dependency-glob: uv.lock" in text
    assert "save-cache: ${{ github.ref == 'refs/heads/main' }}" in text
    assert "uv sync --locked" in text

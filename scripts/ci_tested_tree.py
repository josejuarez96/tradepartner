#!/usr/bin/env python3
"""Can this push to main skip the pytest shards? Prints ``skip`` or ``run`` (#1192).

CI's ``checks-fast`` job runs this on every push to ``main``. A squash merge of a PR whose
branch already contained ``main`` (``ready_pr.py`` merges it in) produces a commit whose
tree is exactly the PR head's tree, and that tree already passed the full sharded suite on
the PR. Running all eight shards again on main adds nothing, so the shards are skipped,
but only when every one of these holds:

1. exactly one merged PR into ``main`` has this commit as its ``merge_commit_sha``;
2. this commit has exactly one parent (a squash), and that parent is an ancestor of the
   PR's head commit (so any ``main`` the PR's CI merged in was older still: the PR run's
   test merge commit had the head's own tree, and so does this commit);
3. this commit's tree equals the PR head's tree;
4. one CI run (check suite) on the PR head, from GitHub Actions, has ``checks`` and every
   ``pytest-shard (i)``, ``i < PYTEST_SHARD_COUNT``, completed with ``success`` (a skipped
   shard is not a pass: a docs-only PR's run never tested the tree).

Fails closed: any other outcome, a failed or odd API answer, a missing input or an
exception prints ``run`` and the full suite runs. The reason goes to stderr, which is the
job log. ``main`` is append-only (the ``protect-main`` ruleset forbids force-pushes and
non-linear history), which is what makes the ancestry in (2) enough.

Usage (CI)::

    GH_TOKEN=... GITHUB_REPOSITORY=owner/repo PYTEST_SHARD_COUNT=8 \\
        python3 scripts/ci_tested_tree.py <sha>

Standard library only: it runs before ``uv sync`` could matter, on the runner's python3.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

MAIN = "main"
GATE_CHECK = "checks"
SHARD_CHECK = "pytest-shard ({})"
ACTIONS_APP = "github-actions"
CHECK_RUNS_PAGE = 100


class Api(Protocol):
    """The GitHub REST reads the decision needs. Faked in tests."""

    def get(self, path: str) -> Any: ...


def _suite_passed_in_full(runs: Sequence[Mapping[str, Any]], shard_count: int) -> bool:
    """Every required check in one suite is present, completed and successful."""
    required = [GATE_CHECK, *(SHARD_CHECK.format(i) for i in range(shard_count))]
    for name in required:
        named = [r for r in runs if r.get("name") == name]
        if not named:
            return False
        for r in named:
            if r.get("status") != "completed" or r.get("conclusion") != "success":
                return False
    return True


def decide(api: Api, repo: str, sha: str, shard_count: int) -> tuple[bool, str]:
    """``(skip, reason)`` for a push of ``sha`` to main; ``skip`` only when all four hold."""
    if shard_count < 1:
        return False, f"bad shard count {shard_count}"

    pulls = api.get(f"repos/{repo}/commits/{sha}/pulls")
    merged = [
        p
        for p in pulls
        if p.get("merge_commit_sha") == sha
        and p.get("merged_at")
        and (p.get("base") or {}).get("ref") == MAIN
    ]
    if len(merged) != 1:
        return False, f"{len(merged)} merged PR(s) into {MAIN} have {sha[:7]} as merge commit"
    pr = merged[0]
    number = pr.get("number")
    head = str((pr.get("head") or {}).get("sha") or "")
    if not head:
        return False, f"PR #{number} has no head sha"

    commit = api.get(f"repos/{repo}/git/commits/{sha}")
    parents = [str(p.get("sha")) for p in commit.get("parents") or []]
    if len(parents) != 1:
        return False, f"{sha[:7]} has {len(parents)} parents, not a squash"
    tree = str((commit.get("tree") or {}).get("sha") or "")

    head_commit = api.get(f"repos/{repo}/git/commits/{head}")
    head_tree = str((head_commit.get("tree") or {}).get("sha") or "")
    if not tree or tree != head_tree:
        return False, f"tree {tree[:7]} differs from PR #{number} head {head[:7]}'s {head_tree[:7]}"

    compare = api.get(f"repos/{repo}/compare/{parents[0]}...{head}")
    if compare.get("status") not in {"ahead", "identical"}:
        return False, (
            f"PR #{number} head {head[:7]} does not contain main's parent {parents[0][:7]} "
            f"(compare status {compare.get('status')!r})"
        )

    page = api.get(
        f"repos/{repo}/commits/{head}/check-runs?filter=latest&per_page={CHECK_RUNS_PAGE}"
    )
    runs = list(page.get("check_runs") or [])
    if int(page.get("total_count", -1)) != len(runs):
        return False, f"check runs on {head[:7]} do not fit one page; not paging"
    suites: dict[object, list[Mapping[str, Any]]] = {}
    for r in runs:
        if (r.get("app") or {}).get("slug") != ACTIONS_APP:
            continue
        suite = (r.get("check_suite") or {}).get("id")
        if suite is None:
            continue
        suites.setdefault(suite, []).append(r)
    for suite, suite_runs in suites.items():
        if _suite_passed_in_full(suite_runs, shard_count):
            return True, (
                f"tree {tree[:7]} is PR #{number}'s head {head[:7]}, which passed checks and "
                f"all {shard_count} pytest shards in check suite {suite}"
            )
    return False, f"no CI run on PR #{number} head {head[:7]} passed checks and every shard"


class GhApi:
    """``gh api`` with the job's token (``GH_TOKEN``)."""

    def get(self, path: str) -> Any:
        out = subprocess.run(
            ["gh", "api", "-H", "Accept: application/vnd.github+json", path],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
        return json.loads(out)


def main(argv: Sequence[str] | None = None, api: Api | None = None) -> int:
    """Print ``skip`` or ``run``; always exit 0 so the workflow reads the word, not a code."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(args) != 1 or not args[0]:
            raise ValueError("usage: ci_tested_tree.py <sha>")
        repo = os.environ["GITHUB_REPOSITORY"]
        shard_count = int(os.environ["PYTEST_SHARD_COUNT"])
        skip, reason = decide(api or GhApi(), repo, args[0], shard_count)
    except Exception as exc:  # fail closed on anything at all
        skip, reason = False, f"lookup failed ({type(exc).__name__}: {exc})"
    print(f"main tested-tree check: {'skip' if skip else 'run'}: {reason}", file=sys.stderr)
    print("skip" if skip else "run")
    return 0


if __name__ == "__main__":
    sys.exit(main())

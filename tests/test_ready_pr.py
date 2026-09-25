"""Tests for scripts/ready_pr.py: conflict resolution, review routing, CI state and the flow.

The flow runs against a fake ``Runner``; git and gh are never called.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ready_pr", Path(__file__).resolve().parents[1] / "scripts" / "ready_pr.py"
)
assert _SPEC is not None and _SPEC.loader is not None
ready_pr = importlib.util.module_from_spec(_SPEC)
sys.modules["ready_pr"] = ready_pr
_SPEC.loader.exec_module(ready_pr)

APPEND_CONFLICT = """## Done
- one
<<<<<<< HEAD
- mine (PR #9)
=======
- theirs (PR #8)
>>>>>>> origin/main

## Teams
"""

DIFF3_CONFLICT = """- one
<<<<<<< HEAD
- mine
||||||| merged common ancestors
=======
- theirs
>>>>>>> origin/main
"""

REAL_CONFLICT = """- one
<<<<<<< HEAD
- mine
=======
**Updated:** 2026-09-25
>>>>>>> origin/main
"""

BODY_OK = """## What & why
x

Closes #69

- [x] `uv run pytest` passes
- [x] Docs updated
"""


# ── pure logic ──────────────────────────────────────────────────────────────────


def test_append_conflicts_keep_both_sides_main_first() -> None:
    out = ready_pr.resolve_append_conflicts(APPEND_CONFLICT)
    assert out == "## Done\n- one\n- theirs (PR #8)\n- mine (PR #9)\n\n## Teams\n"
    assert ready_pr.resolve_append_conflicts(DIFF3_CONFLICT) == "- one\n- theirs\n- mine\n"


def test_append_conflicts_drop_a_line_both_sides_added() -> None:
    text = "<<<<<<< HEAD\n- same\n- mine\n=======\n- same\n>>>>>>> origin/main\n"
    assert ready_pr.resolve_append_conflicts(text) == "- same\n- mine\n"


def test_non_bullet_conflict_is_not_resolved() -> None:
    assert ready_pr.resolve_append_conflicts(REAL_CONFLICT) is None
    assert ready_pr.resolve_append_conflicts("- clean\n") == "- clean\n"


def test_template_boxes_closes_and_branch_issue() -> None:
    body = "- [x] done\n- [ ] Ran it for real\n  - [ ] nested\nCloses #12, fixes #13"
    assert ready_pr.unchecked_boxes(body) == ["Ran it for real", "nested"]
    assert ready_pr.closed_issues(body) == {12, 13}
    assert ready_pr.issue_of_branch("feat/39-as-of") == 39
    assert ready_pr.issue_of_branch("spike/try-duckdb") is None


def test_required_reviews_follow_paths_and_are_found_in_body_or_comments() -> None:
    req = ready_pr.required_reviews(
        ["src/tradepartner/store/asof.py", "src/tradepartner/adapters/fake_broker.py", "docs/x.md"]
    )
    assert req == {"quant-auditor", "safety-reviewer"}
    assert ready_pr.required_reviews(["docs/plans/x.md", "tests/test_team.py"]) == set()
    assert ready_pr.missing_reviews(req, ["body", "Quant-Auditor: no findings"]) == [
        "safety-reviewer"
    ]
    assert ready_pr.missing_reviews(req, ["quant-auditor ok", "safety-reviewer ok"]) == []


def test_checks_state_needs_the_exact_commit_and_completed_runs() -> None:
    hc = ready_pr.HeadChecks
    cr = ready_pr.CheckRun
    good = (cr("checks", "COMPLETED", "SUCCESS"), cr("claims", "COMPLETED", "SUCCESS"))
    assert ready_pr.checks_state(hc("abc", good), "abc") == "success"
    assert ready_pr.checks_state(hc("old", good), "abc") == "pending"
    assert ready_pr.checks_state(hc("abc", ()), "abc") == "pending"
    running = (cr("checks", "IN_PROGRESS", ""), cr("claims", "COMPLETED", "SUCCESS"))
    assert ready_pr.checks_state(hc("abc", running), "abc") == "pending"
    bad = (cr("checks", "COMPLETED", "FAILURE"), cr("claims", "COMPLETED", "SUCCESS"))
    assert ready_pr.checks_state(hc("abc", bad), "abc") == "failure"


# ── the flow, on a fake runner ──────────────────────────────────────────────────


class FakeRunner:
    def __init__(
        self,
        *,
        branch: str = "feat/69-x",
        contains_main: bool = True,
        merge_ok: bool = True,
        conflicted: dict[str, str] | None = None,
        touched: Sequence[str] = ("src/tradepartner/store/asof.py",),
        body: str = BODY_OK,
        comments: Sequence[str] = ("quant-auditor: no findings",),
        checks_after: Sequence[str] = ("success",),
        draft: bool = True,
        dirty: bool = False,
    ) -> None:
        self.branch = branch
        self.contains_main = contains_main
        self.merge_ok = merge_ok
        self.files = dict(conflicted or {})
        self.touched = list(touched)
        self._pr = ready_pr.Pr(69, "feat/69-x", "main", draft, body, tuple(comments))
        self.checks_after = list(checks_after)
        self.dirty = dirty
        self.calls: list[tuple[str, ...]] = []
        self.checks_run: list[tuple[str, ...]] = []
        self.readied: list[int] = []
        self.pushed: list[str] = []

    def git(self, *args: str) -> str:
        self.calls.append(("git", *args))
        match args:
            case ("rev-parse", "--abbrev-ref", "HEAD"):
                return self.branch
            case ("status", "--porcelain"):
                return "M x" if self.dirty else ""
            case ("diff", "--name-only", "--diff-filter=U"):
                return "\n".join(self.files)
            case ("diff", "--name-only", _):
                return "\n".join(self.touched)
            case ("rev-parse", "HEAD"):
                return "abc1234def"
            case ("push", *_):
                self.pushed.append(args[-1])
        return ""

    def git_ok(self, *args: str) -> bool:
        self.calls.append(("git_ok", *args))
        if args[0] == "merge-base":
            return self.contains_main
        if args[0] == "merge":
            return self.merge_ok
        return True

    def run_check(self, cmd: Sequence[str]) -> bool:
        self.checks_run.append(tuple(cmd))
        return True

    def read(self, path: str) -> str:
        return self.files[path]

    def write(self, path: str, text: str) -> None:
        self.files[path] = text

    def pr(self, number: int) -> ready_pr.Pr:
        return self._pr

    def head_checks(self, number: int) -> ready_pr.HeadChecks:
        state = self.checks_after.pop(0) if self.checks_after else "success"
        run = ready_pr.CheckRun(
            "checks", "COMPLETED", "SUCCESS" if state == "success" else "FAILURE"
        )
        if state == "pending":
            return ready_pr.HeadChecks("older", ())
        return ready_pr.HeadChecks("abc1234def", (run,))

    def mark_ready(self, number: int) -> None:
        self.readied.append(number)

    def sleep(self, seconds: float) -> None:
        pass


def test_happy_path_pushes_waits_and_marks_ready() -> None:
    r = FakeRunner(checks_after=["pending", "success"])
    assert ready_pr.ready(r, 69, poll_s=0) == 0
    assert r.pushed == ["HEAD:feat/69-x"]
    assert r.readied == [69]
    assert [c[-1] for c in r.checks_run] == [".", ".", "mypy", "check", "-q"]


def test_wrong_branch_and_dirty_tree_stop_early() -> None:
    with pytest.raises(ready_pr.ReadyError, match="checkout is on"):
        ready_pr.ready(FakeRunner(branch="main"), 69)
    with pytest.raises(ready_pr.ReadyError, match="not clean"):
        ready_pr.ready(FakeRunner(dirty=True), 69)


def test_append_conflict_in_shared_files_is_resolved_and_committed() -> None:
    r = FakeRunner(
        contains_main=False,
        merge_ok=False,
        conflicted={"docs/STATUS.md": APPEND_CONFLICT},
        touched=["src/tradepartner/store/asof.py"],
    )
    assert ready_pr.ready(r, 69, dry_run=True) == 0
    assert "<<<<<<<" not in r.files["docs/STATUS.md"]
    assert ("git", "add", "docs/STATUS.md") in r.calls
    assert ("git", "commit", "--no-edit") in r.calls
    assert r.pushed == []


def test_real_conflict_aborts_the_merge() -> None:
    r = FakeRunner(
        contains_main=False, merge_ok=False, conflicted={"docs/STATUS.md": REAL_CONFLICT}
    )
    with pytest.raises(ready_pr.ReadyError, match="resolve by hand"):
        ready_pr.ready(r, 69)
    assert ("git", "merge", "--abort") in r.calls
    r = FakeRunner(
        contains_main=False, merge_ok=False, conflicted={"src/tradepartner/x.py": "<<<<<<< HEAD"}
    )
    with pytest.raises(ready_pr.ReadyError, match=r"conflicts in src/tradepartner/x\.py"):
        ready_pr.ready(r, 69)
    assert ("git", "merge", "--abort") in r.calls


def test_shared_file_edits_need_the_flag() -> None:
    r = FakeRunner(touched=["docs/STATUS.md", "docs/plans/x.md"])
    with pytest.raises(ready_pr.ReadyError, match="fragments"):
        ready_pr.ready(r, 69, dry_run=True)
    assert ready_pr.ready(r, 69, dry_run=True, allow_shared_files=True) == 0


def test_template_issue_and_reviews_are_enforced() -> None:
    with pytest.raises(ready_pr.ReadyError, match="unticked"):
        ready_pr.ready(FakeRunner(body=BODY_OK + "- [ ] later\n"), 69, dry_run=True)
    with pytest.raises(ready_pr.ReadyError, match="Closes #69"):
        ready_pr.ready(FakeRunner(body=BODY_OK.replace("#69", "#68")), 69, dry_run=True)
    with pytest.raises(ready_pr.ReadyError, match="quant-auditor"):
        ready_pr.ready(FakeRunner(comments=()), 69, dry_run=True)


def test_ci_failure_leaves_the_pr_a_draft() -> None:
    r = FakeRunner(checks_after=["failure"])
    with pytest.raises(ready_pr.ReadyError, match="CI failure"):
        ready_pr.ready(r, 69, poll_s=0)
    assert r.readied == []
    r = FakeRunner(checks_after=["pending", "pending"])
    with pytest.raises(ready_pr.ReadyError, match="timeout"):
        ready_pr.ready(r, 69, poll_s=0, timeout_s=0)

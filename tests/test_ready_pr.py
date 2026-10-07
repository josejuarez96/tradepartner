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
||||||| merged common ancestors
=======
- theirs (PR #8)
>>>>>>> origin/main

## Teams
"""

NO_BASE_CONFLICT = """- one
<<<<<<< HEAD
- mine
=======
- theirs
>>>>>>> origin/main
"""

DELETION_CONFLICT = """- one
<<<<<<< HEAD
- old (PR #1)
- mine
||||||| merged common ancestors
- old (PR #1)
=======
- theirs
>>>>>>> origin/main
"""

REAL_CONFLICT = """- one
<<<<<<< HEAD
- mine
||||||| merged common ancestors
=======
**Updated:** 2026-09-25
>>>>>>> origin/main
"""

TEMPLATE = (Path(__file__).resolve().parents[1] / ".github/pull_request_template.md").read_text()

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


def test_append_conflicts_drop_a_line_both_sides_added() -> None:
    text = "<<<<<<< HEAD\n- same\n- mine\n||||||| base\n=======\n- same\n>>>>>>> origin/main\n"
    assert ready_pr.resolve_append_conflicts(text) == "- same\n- mine\n"


def test_conflicts_with_a_deletion_no_base_or_non_bullets_are_not_resolved() -> None:
    assert ready_pr.resolve_append_conflicts(DELETION_CONFLICT) is None  # would resurrect
    assert ready_pr.resolve_append_conflicts(NO_BASE_CONFLICT) is None  # cannot tell
    assert ready_pr.resolve_append_conflicts(REAL_CONFLICT) is None
    assert ready_pr.resolve_append_conflicts("- clean\n") == "- clean\n"


def test_template_boxes_closes_and_branch_issue() -> None:
    body = "- [x] done\n- [ ] Ran it for real\n  - [ ] nested\nCloses #12, fixes #13"
    assert ready_pr.unchecked_boxes(body) == ["Ran it for real", "nested"]
    assert ready_pr.closed_issues(body) == {12, 13}
    assert ready_pr.issue_of_branch("feat/39-as-of") == 39
    assert ready_pr.issue_of_branch("spike/try-duckdb") is None


def test_required_reviews_follow_paths_and_verdicts_come_from_comments_only() -> None:
    req = ready_pr.required_reviews(
        ["src/tradepartner/store/asof.py", "src/tradepartner/adapters/fake_broker.py", "docs/x.md"]
    )
    assert req == {"quant-auditor", "safety-reviewer"}
    assert ready_pr.required_reviews(["docs/plans/x.md", "tests/test_team.py"]) == set()
    assert ready_pr.required_reviews(["src/tradepartner/adapters/edgar.py"]) == req
    # the template names both agents, so the body never satisfies a review
    assert ready_pr.missing_reviews(req, [TEMPLATE, "ran quant-auditor, fine"]) == sorted(req)
    assert ready_pr.missing_reviews(req, ["Quant-Auditor: PASS\ndetails"]) == ["safety-reviewer"]
    assert ready_pr.missing_reviews(req, ["quant-auditor: pass", "safety-reviewer: PASS"]) == []
    # first line only; latest verdict per agent wins; FAIL is recognised
    assert ready_pr.missing_reviews({"quant-auditor"}, ["notes\nquant-auditor: PASS"]) == [
        "quant-auditor"
    ]
    assert ready_pr.missing_reviews(
        {"quant-auditor"}, ["quant-auditor: PASS", "quant-auditor: FAIL\nregression"]
    ) == ["quant-auditor"]
    assert ready_pr.required_reviews([".github/workflows/ci.yml"]) == {"safety-reviewer"}


def test_pass_with_fixes_counts_only_once_a_later_pass_follows() -> None:
    # #356: PASS WITH FIXES means SHOULD FIX findings are open until the re-review says PASS
    qa = {"quant-auditor"}
    assert ready_pr.missing_reviews(qa, ["quant-auditor: PASS WITH FIXES\nfix x"]) == [
        "quant-auditor"
    ]
    assert ready_pr.missing_reviews(
        qa, ["quant-auditor: FAIL", "quant-auditor: PASS WITH FIXES"]
    ) == ["quant-auditor"]
    assert ready_pr.missing_reviews(
        qa, ["quant-auditor: PASS", "quant-auditor: PASS WITH FIXES"]
    ) == ["quant-auditor"]
    assert (
        ready_pr.missing_reviews(
            qa, ["quant-auditor: PASS WITH FIXES", "fixed in abc", "quant-auditor: PASS"]
        )
        == []
    )
    # the whole first line is the verdict: a variant of PASS WITH FIXES is not a PASS
    for line in (
        "PASS  WITH FIXES",
        "PASS (with fixes)",
        "PASS-WITH-FIXES",
        "PASS / FAIL",
        "PASSED",
        "",
    ):
        assert ready_pr.missing_reviews(qa, [f"quant-auditor: {line}"]) == ["quant-auditor"], line
    assert ready_pr.missing_reviews(qa, ["\nquant-auditor: PASS"]) == ["quant-auditor"]
    for ok in ("quant-auditor:PASS", "  Quant-Auditor:   pass  \r\nbody"):
        assert ready_pr.missing_reviews(qa, [ok]) == [], ok


REPO = Path(__file__).resolve().parents[1]
# Listed on purpose before the module exists; drop an entry once its module lands.
PLANNED_PREFIXES = {
    "src/tradepartner/adapters/alpaca_broker",  # T48c
    "src/tradepartner/llm/",  # Phase 5, ADR 0008
}


def _on_tree(prefix: str) -> bool:
    target = REPO / prefix
    if prefix.endswith("/"):
        return target.is_dir()
    return any(target.parent.glob(target.name + "*"))


def test_every_order_path_module_requires_the_safety_review() -> None:
    src = REPO / "src" / "tradepartner"
    modules = [
        *(src / "execution").glob("*.py"),
        *(src / "adapters").glob("*broker*.py"),
        src / "adapters" / "alpaca_trading_raw.py",
        src / "cli_record.py",
        src / "errors.py",  # the kill-switch and halt-path error types
        REPO / "tests" / "test_ready_pr.py",  # its PLANNED_PREFIXES could hide a rename
    ]
    assert len(modules) > 10
    for m in modules:
        path = m.relative_to(REPO).as_posix()
        assert "safety-reviewer" in ready_pr.required_reviews([path]), path
    assert "safety-reviewer" in ready_pr.required_reviews(
        ["src/tradepartner/adapters/alpaca_broker.py", "src/tradepartner/execution/brokers.py"]
    )


def test_execution_modules_also_require_the_quant_audit() -> None:
    # plan.py, outcomes.py and lots.py compute quantities from store data (#381)
    modules = list((REPO / "src" / "tradepartner" / "execution").glob("*.py"))
    assert len(modules) > 10
    for m in modules:
        path = m.relative_to(REPO).as_posix()
        assert ready_pr.required_reviews([path]) == {"quant-auditor", "safety-reviewer"}, path


def test_merge_train_paths_require_the_safety_review() -> None:
    for path in (
        "scripts/merge_train.py",
        "tests/test_merge_train.py",
        ".github/rulesets/protect-main.json",
    ):
        assert "safety-reviewer" in ready_pr.required_reviews([path]), path


def test_every_review_prefix_exists_on_the_tree_unless_planned() -> None:
    # a rename must not silently disable the gate (#357: exec/ vs execution/)
    for prefix in (*ready_pr.QUANT_PREFIXES, *ready_pr.SAFETY_PREFIXES):
        if prefix not in PLANNED_PREFIXES:
            assert _on_tree(prefix), prefix
    for prefix in PLANNED_PREFIXES:
        assert not _on_tree(prefix), f"{prefix} exists now; drop it from PLANNED_PREFIXES"


def test_edgar_source_requires_the_quant_audit() -> None:
    # it fetches the filing text the store keeps (#382, owner decision 2026-10-04)
    assert ready_pr.required_reviews(["src/tradepartner/adapters/edgar_source.py"]) == {
        "quant-auditor"
    }


def test_uv_lock_requires_the_safety_review() -> None:
    # dependency pins, like pyproject.toml (#382)
    assert ready_pr.required_reviews(["uv.lock"]) == {"safety-reviewer"}


def test_gitignore_requires_no_review() -> None:
    # owner decision on #382: .gitignore gets no required reviewer
    assert ready_pr.required_reviews([".gitignore"]) == set()


def test_shared_list_guard_sees_only_added_bullets_under_the_list_heading() -> None:
    main = "## Done\n- a\n\n## Blocked\n- none\n"
    assert ready_pr.added_list_bullets(main, "## Done\n- a\n- b\n\n## Blocked\n", "## Done") == [
        "- b"
    ]
    assert ready_pr.added_list_bullets(main, "## Done\n- a\n\n## Blocked\n- x\n", "## Done") == []
    assert (
        ready_pr.added_list_bullets(main, "## Done\n\n## Blocked\n", "## Done") == []
    )  # fold trims
    assert ready_pr.is_fold(["docs/STATUS.md", "docs/status.d/1-a.md"], ["docs/status.d/1-a.md"])
    assert not ready_pr.is_fold(["docs/STATUS.md"], [])


def test_missing_fragments_needs_one_file_for_the_issue() -> None:
    assert ready_pr.missing_fragments("feat/69-x", ["src/a.py"]) == ["changelog.d/69-<slug>.md"]
    assert ready_pr.missing_fragments("feat/69-x", ["changelog.d/69-x.md"]) == []
    assert ready_pr.missing_fragments("docs/55-x", ["changelog.d/55-x.md"]) == []
    # The pre-#351 layout still counts during the transition.
    assert ready_pr.missing_fragments("docs/55-x", ["docs/status.d/55-x.md"]) == []
    assert ready_pr.missing_fragments("feat/69-x", ["changelog.d/70-x.md"]) == [
        "changelog.d/69-<slug>.md"
    ]
    assert ready_pr.missing_fragments("spike/x", []) == []


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
    # #1112: `checks` now `needs:` other jobs, so its own check run can be missing from
    # the rollup while jobs it depends on have already finished green. That must read as
    # pending, not success, or a PR could be marked ready before pytest ever started.
    not_yet_queued = (
        cr("checks-fast", "COMPLETED", "SUCCESS"),
        cr("claims", "COMPLETED", "SUCCESS"),
    )
    assert ready_pr.checks_state(hc("abc", not_yet_queued), "abc") == "pending"
    # #1192: a draft's run reports its aggregator under another name and runs no shard;
    # green as it is, it is not the required `checks`, so the head is still pending.
    draft_run = (
        cr("checks-fast", "COMPLETED", "SUCCESS"),
        cr("pytest-shard", "COMPLETED", "SKIPPED"),
        cr("checks (draft, no shards)", "COMPLETED", "SUCCESS"),
        cr("claims", "COMPLETED", "SUCCESS"),
    )
    assert ready_pr.checks_state(hc("abc", draft_run), "abc") == "pending"
    full = (*draft_run, cr("checks", "COMPLETED", "SUCCESS"))
    assert ready_pr.checks_state(hc("abc", full), "abc") == "success"


# ── the flow, on a fake runner ──────────────────────────────────────────────────


class FakeRunner:
    def __init__(
        self,
        *,
        branch: str = "feat/69-x",
        contains_main: bool = True,
        merge_ok: bool = True,
        conflicted: dict[str, str] | None = None,
        touched: Sequence[str] = (
            "src/tradepartner/store/asof.py",
            "docs/status.d/69-x.md",
            "changelog.d/69-x.md",
        ),
        deleted: Sequence[str] = (),
        main_files: dict[str, str] | None = None,
        body: str = BODY_OK,
        comments: Sequence[str] = ("quant-auditor: PASS",),
        checks_after: Sequence[str] = ("success",),
        draft: bool = True,
        dirty: bool = False,
        test_sources: dict[str, str] | None = None,
    ) -> None:
        self.branch = branch
        self.contains_main = contains_main
        self.merge_ok = merge_ok
        self.files = dict(conflicted or {})
        self.touched = list(touched)
        self.deleted = list(deleted)
        self.main_files = dict(main_files or {})
        self._pr = ready_pr.Pr(69, "feat/69-x", "main", draft, body, tuple(comments))
        self.checks_after = list(checks_after)
        self.dirty = dirty
        self.test_sources = dict(test_sources or {})
        self.calls: list[tuple[str, ...]] = []
        self.checks_run: list[tuple[str, ...]] = []
        self.readied: list[int] = []
        self.pushed: list[str] = []
        self.events: list[str] = []
        self.dispatched: list[str] = []

    def git(self, *args: str) -> str:
        self.calls.append(("git", *args))
        match args:
            case ("rev-parse", "--abbrev-ref", "HEAD"):
                return self.branch
            case ("status", "--porcelain"):
                return "M x" if self.dirty else ""
            case ("diff", "--name-only", "--diff-filter=U"):
                return "\n".join(self.files)
            case ("diff", "--name-only", "--diff-filter=D", _) | (
                "diff",
                "--no-renames",
                "--name-only",
                "--diff-filter=D",
                _,
            ):
                return "\n".join(self.deleted)
            case ("-c", "core.quotePath=false", "diff", "--no-renames", "--name-only", _):
                return "\n".join(self.touched)
            case ("show", spec):
                return self.main_files[spec.split(":", 1)[1]]
            case ("rev-parse", "HEAD"):
                return "abc1234def"
            case ("ls-files", "tests"):
                return "\n".join([*self.test_sources, "tests/fixtures/a.json"])
            case ("push", *_):
                self.pushed.append(args[-1])
                self.events.append("push")
        return ""

    def git_ok(self, *args: str) -> bool:
        self.calls.append(("git_ok", *args))
        if args[0] == "merge-base":
            return self.contains_main
        if "merge" in args:
            return self.merge_ok
        if args[0] == "cat-file":
            return args[-1].split(":", 1)[1] in self.main_files
        return True

    def run_check(self, cmd: Sequence[str]) -> bool:
        self.checks_run.append(tuple(cmd))
        return True

    def read(self, path: str) -> str:
        if path in self.test_sources:
            return self.test_sources[path]
        if path not in self.files and path.startswith("changelog.d/"):
            return "- #69 done (PR #70)\n### Added\n- x (#69)\n"
        return self.files[path]

    def write(self, path: str, text: str) -> None:
        self.files[path] = text

    def pr(self, number: int) -> ready_pr.Pr:
        return self._pr

    def head_checks(self, number: int) -> ready_pr.HeadChecks:
        self.events.append("wait")
        state = self.checks_after.pop(0) if self.checks_after else "success"
        run = ready_pr.CheckRun(
            "checks", "COMPLETED", "SUCCESS" if state == "success" else "FAILURE"
        )
        if state == "pending":
            return ready_pr.HeadChecks("older", ())
        return ready_pr.HeadChecks("abc1234def", (run,))

    def mark_ready(self, number: int) -> None:
        self.readied.append(number)
        self.events.append("ready")

    def dispatch_ci(self, branch: str) -> None:
        self.dispatched.append(branch)
        self.events.append("dispatch")

    def sleep(self, seconds: float) -> None:
        pass


def test_happy_path_pushes_waits_and_marks_ready() -> None:
    r = FakeRunner(checks_after=["pending", "success"])
    assert ready_pr.ready(r, 69, poll_s=0) == 0
    assert r.pushed == ["HEAD:feat/69-x"]
    assert r.readied == [69]
    assert [c[-1] for c in r.checks_run] == [".", ".", "mypy", "check", BUDGET]


def test_a_draft_gets_a_dispatched_full_run_before_it_is_marked_ready() -> None:
    # #1192: a draft's own CI skips the shards and never reports `checks`, so ready_pr
    # dispatches the full run on the pushed head, waits for it, and only then marks ready.
    r = FakeRunner(checks_after=["pending", "success"])
    assert ready_pr.ready(r, 69, poll_s=0) == 0
    assert r.dispatched == ["feat/69-x"]
    assert r.events == ["push", "dispatch", "wait", "wait", "ready"]


def test_a_failed_full_run_leaves_the_draft_a_draft() -> None:
    r = FakeRunner(checks_after=["failure"])
    with pytest.raises(ready_pr.ReadyError, match="CI failure"):
        ready_pr.ready(r, 69, poll_s=0)
    assert r.dispatched == ["feat/69-x"]
    assert r.readied == []


def test_a_ready_pr_runs_the_full_suite_on_its_own_push_and_is_not_dispatched() -> None:
    r = FakeRunner(draft=False)
    assert ready_pr.ready(r, 69, poll_s=0) == 0
    assert r.dispatched == []
    assert r.readied == []


def test_dry_run_dispatches_nothing() -> None:
    r = FakeRunner()
    assert ready_pr.ready(r, 69, dry_run=True) == 0
    assert r.dispatched == [] and r.pushed == []


def test_wrong_branch_main_branch_and_dirty_tree_stop_early() -> None:
    with pytest.raises(ready_pr.ReadyError, match="checkout is on"):
        ready_pr.ready(FakeRunner(branch="main"), 69)
    r = FakeRunner(branch="main")
    r._pr = ready_pr.Pr(69, "main", "main", True, BODY_OK, ())
    with pytest.raises(ready_pr.ReadyError, match="feature branches"):
        ready_pr.ready(r, 69)
    assert not [c for c in r.calls if c[0] == "git" and c[1] in ("push", "merge", "commit")]
    with pytest.raises(ready_pr.ReadyError, match="not clean"):
        ready_pr.ready(FakeRunner(dirty=True), 69)


def test_append_conflict_in_shared_files_is_resolved_and_committed() -> None:
    r = FakeRunner(
        contains_main=False,
        merge_ok=False,
        conflicted={"docs/STATUS.md": APPEND_CONFLICT},
    )
    assert ready_pr.ready(r, 69, dry_run=True) == 0
    assert (
        "git_ok",
        "-c",
        "merge.conflictStyle=diff3",
        "merge",
        "--no-edit",
        "origin/main",
    ) in r.calls
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
    # modify/delete: the file has no markers, so nothing is resolved or staged
    r = FakeRunner(contains_main=False, merge_ok=False, conflicted={"CHANGELOG.md": "- ours\n"})
    with pytest.raises(ready_pr.ReadyError, match="without markers"):
        ready_pr.ready(r, 69)
    assert ("git", "merge", "--abort") in r.calls
    assert ("git", "add", "CHANGELOG.md") not in r.calls


def test_added_done_bullets_need_fragments_unless_fold_or_flag() -> None:
    main = "## Recently done\n- a\n\n## Blocked\n- none\n"
    frags = ["changelog.d/69-x.md"]
    added = FakeRunner(
        touched=["docs/STATUS.md", *frags],
        main_files={"docs/STATUS.md": main},
        conflicted={"docs/STATUS.md": "## Recently done\n- a\n- mine\n\n## Blocked\n- none\n"},
    )
    with pytest.raises(ready_pr.ReadyError, match="adds lines to the shared lists"):
        ready_pr.ready(added, 69, dry_run=True)
    assert ready_pr.ready(added, 69, dry_run=True, allow_shared_files=True) == 0

    blocked_only = FakeRunner(
        touched=["docs/STATUS.md", *frags],
        main_files={"docs/STATUS.md": main},
        conflicted={"docs/STATUS.md": "## Recently done\n- a\n\n## Blocked\n- waiting on T3\n"},
    )
    assert ready_pr.ready(blocked_only, 69, dry_run=True) == 0

    fold = FakeRunner(
        touched=["docs/STATUS.md", "changelog.d/1-a.md", *frags],
        deleted=["changelog.d/1-a.md"],
        main_files={"docs/STATUS.md": main},
        conflicted={"docs/STATUS.md": "## Recently done\n- a\n- folded\n\n## Blocked\n- none\n"},
    )
    assert ready_pr.ready(fold, 69, dry_run=True) == 0

    no_main_file = FakeRunner(touched=["CHANGELOG.md", *frags], conflicted={"CHANGELOG.md": "x"})
    assert ready_pr.ready(no_main_file, 69, dry_run=True) == 0


def test_missing_fragment_stops_the_run() -> None:
    r = FakeRunner(touched=["src/tradepartner/store/asof.py"])
    with pytest.raises(ready_pr.ReadyError, match=r"changelog\.d/69-<slug>\.md"):
        ready_pr.ready(r, 69, dry_run=True)


def test_template_issue_and_reviews_are_enforced() -> None:
    with pytest.raises(ready_pr.ReadyError, match="unticked"):
        ready_pr.ready(FakeRunner(body=BODY_OK + "- [ ] later\n"), 69, dry_run=True)
    with pytest.raises(ready_pr.ReadyError, match="Closes #69"):
        ready_pr.ready(FakeRunner(body=BODY_OK.replace("#69", "#68")), 69, dry_run=True)
    with pytest.raises(ready_pr.ReadyError, match="quant-auditor"):
        ready_pr.ready(FakeRunner(comments=()), 69, dry_run=True)
    with pytest.raises(ready_pr.ReadyError, match="quant-auditor"):
        ready_pr.ready(FakeRunner(comments=("quant-auditor ran, looks fine",)), 69, dry_run=True)


def test_redact_strips_credentials_from_urls() -> None:
    assert ready_pr._redact("fatal: https://x:ghp_abc@github.com/a/b\n") == (
        "fatal: https://***@github.com/a/b"
    )
    assert ready_pr._redact("plain error") == "plain error"


def test_ci_failure_leaves_the_pr_a_draft() -> None:
    r = FakeRunner(checks_after=["failure"])
    with pytest.raises(ready_pr.ReadyError, match="CI failure"):
        ready_pr.ready(r, 69, poll_s=0)
    assert r.readied == []
    r = FakeRunner(checks_after=["pending", "pending"])
    with pytest.raises(ready_pr.ReadyError, match="timeout"):
        ready_pr.ready(r, 69, poll_s=0, timeout_s=0)


def test_tests_needed_only_for_code_tests_scripts_and_deps() -> None:
    for code in (
        ["src/tradepartner/store/asof.py"],
        ["tests/test_team.py"],
        ["scripts/ready_pr.py"],
        ["pyproject.toml"],
        ["uv.lock"],
        [".github/workflows/ci.yml"],
        [".github/pull_request_template.md"],
        [".python-version"],
        ["docs/STATUS.md", "src/tradepartner/x.py"],
    ):
        assert ready_pr.tests_needed(code), code
    for docs in (
        [],
        ["docs/plans/phase-2.md", "docs/status.d/78-x.md", "changelog.d/78-x.md"],
        ["CLAUDE.md", ".claude/skills/ready-pr/SKILL.md", "README.md"],
        ["docs/src/notes.md", "srcfile.txt", "pyproject.toml.bak", "sub/uv.lock"],
    ):
        assert not ready_pr.tests_needed(docs), docs


BUDGET = "tests/test_docs_budget.py"


def _ran_pytest(r: FakeRunner) -> bool:
    return any("pytest" in c for c in r.checks_run)


def _ran_suite(r: FakeRunner) -> bool:
    """pytest beyond the always-run docs-budget check."""
    return any("pytest" in c and c[-1] != BUDGET for c in r.checks_run)


def test_pytest_runs_locally_only_when_needed_unless_forced_or_skipped() -> None:
    docs_only = ["docs/plans/p.md", "docs/status.d/69-x.md", "changelog.d/69-x.md"]
    code = FakeRunner()
    assert ready_pr.ready(code, 69, dry_run=True) == 0
    assert not _ran_suite(code)  # CI runs the full suite on a code diff (#1130)
    assert [c[-1] for c in code.checks_run] == [".", ".", "mypy", "check", BUDGET]

    code_forced = FakeRunner()
    assert ready_pr.ready(code_forced, 69, dry_run=True, run_tests=True) == 0
    assert [c[-1] for c in code_forced.checks_run] == [".", ".", "mypy", "check", BUDGET, "-q"]

    docs = FakeRunner(touched=docs_only, comments=())
    assert ready_pr.ready(docs, 69, dry_run=True) == 0
    assert not _ran_suite(docs)
    assert [c[-1] for c in docs.checks_run] == [".", ".", "mypy", "check", BUDGET]

    forced = FakeRunner(touched=docs_only, comments=())
    assert ready_pr.ready(forced, 69, dry_run=True, run_tests=True) == 0
    assert _ran_pytest(forced)

    skipped = FakeRunner()
    assert ready_pr.ready(skipped, 69, dry_run=True, run_tests=False) == 0
    assert not _ran_suite(skipped)


def test_pytest_decision_is_printed(capsys: pytest.CaptureFixture[str]) -> None:
    ready_pr.ready(
        FakeRunner(touched=["docs/a.md", "docs/status.d/69-x.md", "changelog.d/69-x.md"]),
        69,
        dry_run=True,
    )
    assert "skipping local pytest" in capsys.readouterr().out
    ready_pr.ready(FakeRunner(), 69, dry_run=True)
    assert "CI runs the full suite" in capsys.readouterr().out
    ready_pr.ready(FakeRunner(), 69, dry_run=True, run_tests=False)
    assert "--no-tests" in capsys.readouterr().out


def test_cli_tests_flags_are_exclusive() -> None:
    p = ready_pr.build_parser()
    assert p.parse_args(["5"]).tests is None
    assert p.parse_args(["5", "--tests"]).tests is True
    assert p.parse_args(["5", "--no-tests"]).tests is False
    with pytest.raises(SystemExit):
        p.parse_args(["5", "--tests", "--no-tests"])
    assert p.parse_args(["5"]).full_tests is False
    assert p.parse_args(["5", "--full-tests"]).full_tests is True
    for other in ("--tests", "--no-tests"):
        with pytest.raises(SystemExit):
            p.parse_args(["5", "--full-tests", other])


def test_cli_tests_needed_reads_paths_from_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CI pipes `git diff --name-only` in and gates its Tests step on the answer."""
    import io

    for stdin, answer in (
        ("docs/plans/phase-2.md\nchangelog.d/78-x.md\n", "no"),
        ("docs/STATUS.md\nsrc/tradepartner/x.py\n", "yes"),
        ("", "no"),
    ):
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        assert ready_pr.main(["--tests-needed"]) == 0
        assert capsys.readouterr().out.strip() == answer, stdin


def test_cli_needs_a_pr_unless_tests_needed() -> None:
    with pytest.raises(SystemExit):
        ready_pr.main([])


def test_touched_paths_list_both_sides_of_a_rename() -> None:
    """A file moved out of `src/` must still count as touching `src/`: git's default
    rename detection would list only the new path. Non-ASCII paths come unquoted."""
    r = FakeRunner()
    ready_pr.ready(r, 69, dry_run=True)
    diffs = [c for c in r.calls if "diff" in c and "--diff-filter=U" not in c]
    touched = [c for c in diffs if "--diff-filter=D" not in c]
    assert touched, r.calls
    for call in touched:
        assert "--no-renames" in call, call
        assert call[1:3] == ("-c", "core.quotePath=false"), call


def test_lacks_changelog_bullets_only_matters_on_feat_and_fix() -> None:
    status_only = "- #69 done (PR #70)\n"
    full = "- #69 done\n### Fixed\n- f (#69)\n"
    assert ready_pr.lacks_changelog_bullets("feat/69-x", [status_only])
    assert ready_pr.lacks_changelog_bullets("fix/69-x", [])
    assert ready_pr.lacks_changelog_bullets("fix/69-x", ["### Added\n"])
    assert not ready_pr.lacks_changelog_bullets("fix/69-x", [full])
    assert not ready_pr.lacks_changelog_bullets("docs/69-x", [status_only])


def test_a_feat_pr_with_a_status_only_fragment_is_not_ready() -> None:
    r = FakeRunner(
        touched=["src/tradepartner/x.py", "changelog.d/69-x.md"],
        conflicted={"changelog.d/69-x.md": "- #69 done (PR #70)\n"},
    )
    with pytest.raises(ready_pr.ReadyError, match="records its change in CHANGELOG"):
        ready_pr.ready(r, 69, dry_run=True)
    legacy_only = FakeRunner(touched=["src/tradepartner/x.py", "docs/status.d/69-x.md"])
    with pytest.raises(ready_pr.ReadyError, match="records its change in CHANGELOG"):
        ready_pr.ready(legacy_only, 69, dry_run=True)


# ── targeted local tests (#456) ─────────────────────────────────────────────────

SOURCES = {
    "tests/store/test_asof.py": "from tradepartner.store.asof import as_of\n",
    "tests/lookahead/test_la.py": "from tradepartner.store import (\n    db,\n    asof,\n)\n",
    "tests/store/test_journal.py": "from tradepartner.store.journal import fills_for\n",
    "tests/store/test_other.py": "import tradepartner.store.asof_extra\n",
    "tests/test_config.py": "from tradepartner.config import Settings\n",
    "tests/test_ready_pr.py": 'SPEC = ROOT / "scripts" / "ready_pr.py"\n',
    "tests/test_docs_budget.py": "from tradepartner.config import Settings\n",
    "tests/execution/test_boundaries.py": "SRC = ROOT / 'src'\n",
    "tests/execution/test_sdk_boundary.py": "SRC = ROOT / 'src'\n",
    "tests/test_no_literals.py": "SRC = ROOT / 'src'\n",
    "tests/test_no_forbidden_imports.py": "SRC = ROOT / 'src'\n",
    "tests/backtest/test_store_provider.py": "from tradepartner.backtest import store_provider\n",
    "tests/backtest/test_costs.py": "from tradepartner.backtest.costs import cost\n",
    "tests/test_ci_workflow.py": 'CI = ROOT / ".github" / "workflows" / "ci.yml"\n',
}
SRC_WIDE = (
    "tests/execution/test_boundaries.py",
    "tests/execution/test_sdk_boundary.py",
    "tests/test_no_forbidden_imports.py",
    "tests/test_no_literals.py",
)


def test_a_changed_module_runs_the_tests_that_import_it_and_the_tree_scans() -> None:
    got = ready_pr.targeted_tests(["src/tradepartner/store/asof.py"], SOURCES)
    assert got == tuple(
        sorted({"tests/store/test_asof.py", "tests/lookahead/test_la.py", *SRC_WIDE})
    )
    # a top-level module, and the docs-budget test is never in the targeted list
    assert ready_pr.targeted_tests(["src/tradepartner/config.py"], SOURCES) == tuple(
        sorted({"tests/test_config.py", *SRC_WIDE})
    )


def test_changed_tests_and_scripts_run_themselves_or_their_tests() -> None:
    assert ready_pr.targeted_tests(["tests/store/test_journal.py"], SOURCES) == (
        "tests/store/test_journal.py",
    )
    assert ready_pr.targeted_tests(["scripts/ready_pr.py"], SOURCES) == ("tests/test_ready_pr.py",)
    # a deleted or renamed-away test file (not tracked any more) is not run
    assert ready_pr.targeted_tests(
        ["tests/store/test_gone.py", "tests/store/test_journal.py"], SOURCES
    ) == ("tests/store/test_journal.py",)
    # a .github file runs the tests that read it; one no test names maps to nothing
    assert ready_pr.targeted_tests([".github/workflows/ci.yml"], SOURCES) == (
        "tests/test_ci_workflow.py",
    )
    assert ready_pr.targeted_tests([".github/ISSUE_TEMPLATE/bug.yml"], SOURCES) == ()


def test_paths_no_test_can_fail_map_to_nothing() -> None:
    for paths in (["docs/plans/p.md", "changelog.d/69-x.md"], [".github/rulesets/main.json"]):
        assert ready_pr.targeted_tests(paths, SOURCES) == (), paths


@pytest.mark.parametrize(
    "path",
    [
        "tests/conftest.py",
        "tests/execution/conftest.py",
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "tests/fixtures/alpaca/orders.json",
        "tests/helpers.py",
        "src/tradepartner/store/__init__.py",
        "src/tradepartner/py.typed",
        "src/tradepartner/backtest/gone.py",  # no test names it: the mapping is unclear
        "scripts/no_tests_name_me.py",
    ],
)
def test_unclear_mappings_fall_back_to_the_full_suite(path: str) -> None:
    assert ready_pr.targeted_tests([path, "scripts/ready_pr.py"], SOURCES) is None


def test_a_backtest_module_also_runs_the_backtest_subtree_scan() -> None:
    assert ready_pr.targeted_tests(["src/tradepartner/backtest/costs.py"], SOURCES) == tuple(
        sorted({"tests/backtest/test_costs.py", "tests/backtest/test_store_provider.py", *SRC_WIDE})
    )


def test_a_deleted_module_falls_back_to_the_full_suite() -> None:
    gone = "src/tradepartner/store/asof.py"
    assert ready_pr.targeted_tests([gone], SOURCES, deleted=[gone]) is None


def test_tests_runs_only_the_mapped_tests_unless_full_tests(
    capsys: pytest.CaptureFixture[str],
) -> None:
    targeted = FakeRunner(test_sources=SOURCES)
    assert ready_pr.ready(targeted, 69, dry_run=True, run_tests=True) == 0
    assert targeted.checks_run[-1] == (
        *ready_pr.PYTEST_CHECK,
        *sorted({"tests/store/test_asof.py", "tests/lookahead/test_la.py", *SRC_WIDE}),
    )
    assert "targeted" in capsys.readouterr().out

    full = FakeRunner(test_sources=SOURCES)
    assert ready_pr.ready(full, 69, dry_run=True, full_tests=True) == 0
    assert full.checks_run[-1] == ready_pr.PYTEST_CHECK
    assert "--full-tests" in capsys.readouterr().out

    unclear = FakeRunner(test_sources=SOURCES, touched=["uv.lock", "changelog.d/69-x.md"])
    unclear._pr = ready_pr.Pr(69, "feat/69-x", "main", True, BODY_OK, ("safety-reviewer: PASS",))
    assert ready_pr.ready(unclear, 69, dry_run=True, run_tests=True) == 0
    assert unclear.checks_run[-1] == ready_pr.PYTEST_CHECK

    ci_only = FakeRunner(touched=[".github/ISSUE_TEMPLATE/bug.yml", "changelog.d/69-x.md"])
    ci_only._pr = ready_pr.Pr(69, "feat/69-x", "main", True, BODY_OK, ("safety-reviewer: PASS",))
    assert ready_pr.ready(ci_only, 69, dry_run=True) == 0
    assert not _ran_suite(ci_only)
    assert "CI runs the full suite" in capsys.readouterr().out


def test_a_moved_module_counts_as_deleted_at_its_old_path() -> None:
    moved = FakeRunner(
        test_sources=SOURCES,
        touched=[
            "src/tradepartner/store/asof.py",
            "src/tradepartner/store/asof2.py",
            "changelog.d/69-x.md",
        ],
        deleted=["src/tradepartner/store/asof.py"],
    )
    assert ready_pr.ready(moved, 69, dry_run=True, run_tests=True) == 0
    assert moved.checks_run[-1] == ready_pr.PYTEST_CHECK
    assert (
        "git",
        "diff",
        "--no-renames",
        "--name-only",
        "--diff-filter=D",
        "origin/main...HEAD",
    ) in moved.calls


def test_full_suite_runs_in_parallel_only_with_xdist() -> None:
    """#581: `-n auto` is added to the bare full-suite command, never to a targeted run."""
    full = ready_pr.PYTEST_CHECK
    assert ready_pr.parallel_if_full_suite(full, xdist=True) == (*full, "-n", "auto")
    assert ready_pr.parallel_if_full_suite(full, xdist=False) == full
    targeted = (*full, "tests/test_cli.py")
    assert ready_pr.parallel_if_full_suite(targeted, xdist=True) == targeted
    other = ("uv", "run", "mypy")
    assert ready_pr.parallel_if_full_suite(other, xdist=True) == other

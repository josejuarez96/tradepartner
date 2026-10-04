"""Merge train, pure core (plan T72; spec docs/specs/merge-train.md reqs 1, 2, 4 to 8).

No network, no git: every function under test is pure. AC numbers are the spec's.
"""

from __future__ import annotations

import dataclasses
import fcntl
import importlib.util
import json
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ready_pr = sys.modules.get("ready_pr") or _load("ready_pr")
mt = _load("merge_train")

OWNER = "josejuarez96"
REPO = f"{OWNER}/tradepartner"
HEAD = "a" * 40
BODY = "Closes #12\n\n- [x] done\n"
FRAGMENT = "status: x\n\n### Added\n- a thing (#12)\n"


def _pr(**overrides: object) -> object:
    fields: dict[str, object] = {
        "number": 30,
        "author": OWNER,
        "head_repo": REPO,
        "repo": REPO,
        "state": "OPEN",
        "is_draft": False,
        "base": "main",
        "labels": (),
        "head": HEAD,
        "branch": "feat/12-thing",
        "body": BODY,
        "fragment_texts": (FRAGMENT,),
    }
    fields.update(overrides)
    return mt.PullRequest(**fields)


def _checks(
    conclusion: str = "SUCCESS", sha: str = HEAD, names: tuple[str, ...] = ("checks", "claims")
) -> object:
    runs = tuple(ready_pr.CheckRun(name, "COMPLETED", conclusion) for name in names)
    return ready_pr.HeadChecks(sha, runs)


def _c(body: str, author: str = OWNER) -> object:
    return mt.Comment(author=author, body=body)


PATHS = ("scripts/team.py", "changelog.d/12-thing.md")
SAFETY_PATHS = ("src/tradepartner/execution/wrapper.py", "changelog.d/12-thing.md")


def _eligible(
    pr: object | None = None, checks: object | None = None, paths=PATHS, comments=()
) -> str | None:
    return mt.eligibility(pr or _pr(), checks or _checks(), paths, comments)


# --- AC1, AC1b, AC2: eligibility --------------------------------------------------------


def test_a_ready_pr_is_eligible_even_when_behind_main() -> None:
    """AC2: nothing in the checks reads how far behind `main` the PR is."""
    assert _eligible() is None


@pytest.mark.parametrize(
    ("pr", "checks", "paths", "comments", "check"),
    [
        (_pr(head_repo="someone/fork"), None, PATHS, (), "(0)"),
        (_pr(author="someone"), None, PATHS, (), "(0)"),
        (_pr(is_draft=True), None, PATHS, (), "(a)"),
        (_pr(state="CLOSED"), None, PATHS, (), "(a)"),
        (_pr(base="develop"), None, PATHS, (), "(b)"),
        (_pr(labels=("parked",)), None, PATHS, (), "(c)"),
        (
            None,
            None,
            PATHS,
            (mt.comment("CULPRIT", "20261001-120000-abcdef1", head=HEAD),),
            "(d)",
        ),
        (None, _checks("FAILURE"), PATHS, (), "(e)"),
        (None, _checks(names=("checks",)), PATHS, (), "(e)"),
        (None, _checks(sha="b" * 40), PATHS, (), "(e)"),
        (_pr(body="Closes #12\n- [ ] Ran it\n"), None, PATHS, (), "(f)"),
        (_pr(body="- [x] done\n"), None, PATHS, (), "(g)"),
        (None, None, ("scripts/team.py",), (), "(h)"),
        (_pr(fragment_texts=("status only\n",)), None, PATHS, (), "(h)"),
        (None, None, SAFETY_PATHS, ("safety-reviewer: PASS WITH FIXES",), "(i)"),
    ],
)
def test_each_ineligibility_reason(pr, checks, paths, comments, check) -> None:
    """AC1: each failing check is the one reason, named by its req 1 label."""
    reason = _eligible(pr, checks, paths, tuple(_c(b) for b in comments))
    assert reason is not None and reason.startswith(check)


def test_the_first_failing_check_in_req_1_order_is_the_reason() -> None:
    pr = _pr(author="someone", is_draft=True, labels=("parked",), body="- [ ] x\n")
    assert _eligible(pr, _checks("FAILURE")).startswith("(0)")
    pr = _pr(is_draft=True, labels=("parked",), body="- [ ] x\n")
    assert _eligible(pr, _checks("FAILURE")).startswith("(a)")
    assert _eligible(_pr(labels=("parked",), body="- [ ] x\n"), _checks("FAILURE")).startswith(
        "(c)"
    )


def test_a_culprit_is_eligible_again_once_its_head_changes_and_ineligible_does_not_hide_it() -> (
    None
):
    culprit = _c(mt.comment("CULPRIT", "20261001-120000-abcdef1", head=HEAD))
    ineligible = _c("merge-train: INELIGIBLE batch 20261002-120000-abcdef1\n(d) culprit")
    assert _eligible(comments=(culprit, ineligible)).startswith("(d)")
    assert _eligible(_pr(head="c" * 40), _checks(sha="c" * 40), comments=(culprit,)) is None
    later = _c("merge-train: HELD batch 20261002-120000-abcdef1\nafter the culprit")
    assert _eligible(comments=(culprit, later)) is None


def test_only_the_owners_comments_count() -> None:
    """AC1b: a pasted verdict or a forged train line from another login is invisible."""
    forged_pass = _c("safety-reviewer: PASS", author="someone")
    assert _eligible(paths=SAFETY_PATHS, comments=(forged_pass,)).startswith("(i)")
    assert _eligible(paths=SAFETY_PATHS, comments=(_c("safety-reviewer: PASS"),)) is None
    forged_culprit = _c(f"merge-train: CULPRIT batch x\nhead {HEAD}", author="someone")
    assert _eligible(comments=(forged_culprit,)) is None


# --- AC3: order --------------------------------------------------------------------------


def test_the_default_order_is_ascending_and_an_explicit_order_is_kept() -> None:
    assert mt.order_batch([12, 7, 9], None) == [7, 9, 12]
    assert mt.order_batch([12, 7, 9], [12, 7, 9]) == [12, 7, 9]
    with pytest.raises(ValueError):
        mt.order_batch([12, 7, 9], [12, 7])
    with pytest.raises(ValueError):
        mt.order_batch([7, 7], None)


def test_the_batch_id() -> None:
    built = datetime(2026, 10, 1, 9, 5, 7, tzinfo=UTC)
    assert mt.batch_id(built, "0123456789abcdef") == "20261001-090507-0123456"
    with pytest.raises(ValueError):
        mt.batch_id(datetime(2026, 10, 1), "0123456")  # noqa: DTZ001


# --- AC7: run classification -------------------------------------------------------------


def _steps(**conclusions: str) -> list[object]:
    names = {"Set_up_job": "Set up job", "Hygiene": "Hygiene (pre-commit hooks)"}
    return [mt.Step(names.get(k, k), v) for k, v in conclusions.items()]


@pytest.mark.parametrize(
    ("steps", "duration", "status", "conclusion", "kind", "detail"),
    [
        (_steps(Install="success", Tests="success"), 900, "completed", "success", "green", None),
        (
            _steps(Install="success", Lint="failure", Tests="skipped"),
            300,
            "completed",
            "failure",
            "red",
            "Lint",
        ),
        (
            _steps(Install="success", Hygiene="failure"),
            30,
            "completed",
            "failure",
            "red",
            "Hygiene",
        ),
        (
            _steps(Install="failure", Hygiene="skipped", Lint="skipped", Tests="skipped"),
            120,
            "completed",
            "failure",
            "inconclusive",
            "Install failed",
        ),
        (
            _steps(Set_up_job="failure"),
            5,
            "completed",
            "failure",
            "inconclusive",
            "Set up job failed",
        ),
        ([], 4, "completed", "failure", "inconclusive", "under 60 s"),
        (
            _steps(Install="success", Tests="success"),
            600,
            "completed",
            "cancelled",
            "inconclusive",
            "cancelled",
        ),
        ([], 0, None, None, "inconclusive", "no run"),
        (_steps(Install="success"), 3600, "in_progress", None, "inconclusive", "timed out"),
    ],
)
def test_classify_run(steps, duration, status, conclusion, kind, detail) -> None:
    outcome = mt.classify_run(steps, duration, status, conclusion)
    assert outcome.kind == kind
    assert (outcome.detail is None) if detail is None else (detail in outcome.detail)


def test_the_red_steps_are_the_ci_workflow_names() -> None:
    """A renamed CI step must fail here, not turn every failure inconclusive."""
    ci = (_SCRIPTS.parent / ".github" / "workflows" / "ci.yml").read_text()
    names = {m.split(" (")[0] for m in re.findall(r"^\s*- name: (.+)$", ci, re.MULTILINE)}
    assert set(mt.RED_STEPS) <= names
    assert mt.RED_STEPS == ("Hygiene", "Fragments", "Lint", "Format", "Types", "Tests")


# --- AC8: bisect -----------------------------------------------------------------------


def _bisect(n: int, culprit: int) -> tuple[list[int], int, list[int]]:
    """Run the bisect against a CI that fails every prefix containing `culprit`."""
    green, red, probes = 0, n, []
    while (k := mt.next_probe(green, red)) is not None:
        probes.append(k)
        if k >= culprit:
            red = k
        else:
            green = k
    found, held = mt.bisect_result(green, red, list(range(1, n + 1)))
    return probes, found, held


def test_the_bisect_sequence_for_five_and_eight() -> None:
    """AC8: probes 2, 3, 4 for n = 5 with PR 4 the culprit (3 = ceil(log2 5) probes)."""
    assert _bisect(5, 4) == ([2, 3, 4], 4, [5])
    assert _bisect(8, 6) == ([4, 6, 5], 6, [7, 8])
    assert _bisect(8, 1) == ([4, 2, 1], 1, [2, 3, 4, 5, 6, 7, 8])
    assert mt.probe_branch("20261001-090507-0123456", 3) == "train/20261001-090507-0123456-p3"
    with pytest.raises(ValueError):
        mt.bisect_result(1, 4, [1, 2, 3, 4])  # bounds not adjacent: no culprit yet


# --- records, AC11 and AC13 --------------------------------------------------------------


def _record(green: list[int], n: int = 4, outcome: str = "green") -> object:
    prs = [
        mt.PrEntry(
            number=10 + i,
            head=f"{i}" * 40,
            branch=f"feat/{10 + i}-x",
            issue=10 + i,
            state="accepted",
        )
        for i in range(1, n + 1)
    ]
    return mt.Record(
        batch_id="20261001-090507-0123456",
        built_at="2026-10-01T09:05:07+00:00",
        base="0123456789",
        main_green_at_base=True,
        requested="all",
        prs=prs,
        trees=[f"t{k}" for k in range(n + 1)],
        train_branch="train/20261001-090507-0123456",
        outcome=outcome,
        green_prefixes=green,
    )


def test_a_record_round_trips_through_json() -> None:
    record = _record([2, 4])
    record.probes.append(
        mt.Probe(
            k=2, branch="train/x-p2", sha="s", run_id=1, run_attempt=1, run_url="u", outcome="green"
        )
    )
    record.prs[0] = mt.PrEntry(
        number=9,
        head="h",
        branch="b",
        issue=None,
        state="dropped",
        reason="conflict",
        paths=["a.py"],
        conflicts_with=[7],
    )
    assert mt.Record.from_json(record.to_json()) == record


def test_the_mergeable_prefix_shrinks_to_a_green_probe() -> None:
    """AC11: PR 3 of a green batch of four changed head."""
    still_valid = [True, True, False, True]
    assert mt.mergeable_prefix(_record([4]), still_valid) == 0
    assert mt.mergeable_prefix(_record([2, 4]), still_valid) == 2
    assert mt.mergeable_prefix(_record([2, 4]), [True] * 4) == 4


def _tested(record: object) -> dict[int, list[str]]:
    """The `TESTED` comments `build` posts for `record`."""
    out: dict[int, list[str]] = {}
    for k, pr in enumerate(record.prs, start=1):
        prefixes = {g: f"https://ci/{g}" for g in record.green_prefixes if g >= k}
        if prefixes:
            out[pr.number] = [
                mt.comment("TESTED", record.batch_id, position=k, head=pr.head, prefixes=prefixes)
            ]
    return out


def test_the_record_must_match_the_tested_comments() -> None:
    """AC13: green batch, red batch with a probe, swapped position, edited head or prefixes."""
    green = _record([4])
    assert mt.record_matches_comments(green, _tested(green))
    red = _record([2], outcome="red")  # PR 3 culprit, PR 4 held: no TESTED for them
    assert mt.record_matches_comments(red, _tested(red))

    swapped = _record([4])
    swapped.prs[0], swapped.prs[1] = swapped.prs[1], swapped.prs[0]
    assert not mt.record_matches_comments(swapped, _tested(green))
    edited = _record([4])
    edited.prs[2] = mt.PrEntry(
        number=13, head="f" * 40, branch="feat/13-x", issue=13, state="accepted"
    )
    assert not mt.record_matches_comments(edited, _tested(green))
    widened = _record([2, 4], outcome="red")
    assert not mt.record_matches_comments(widened, _tested(red))
    extra = _tested(red)
    extra[14] = [
        mt.comment("TESTED", red.batch_id, position=4, head=red.prs[3].head, prefixes={4: "u"})
    ]
    assert not mt.record_matches_comments(red, extra)


# --- comments ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome", ["TESTED", "MERGED", "DROPPED", "CULPRIT", "HELD", "INCONCLUSIVE", "INELIGIBLE"]
)
def test_every_comment_starts_with_the_train_line(outcome: str) -> None:
    detail: dict[str, object] = {"reason": "r"}
    if outcome == "TESTED":
        detail = {"position": 1, "head": HEAD, "prefixes": {1: "u"}}
    elif outcome == "CULPRIT":
        detail = {"head": HEAD, "prefix": 2, "probe": "u"}
    text = mt.comment(outcome, "20261001-090507-0123456", **detail)
    assert text.splitlines()[0] == f"merge-train: {outcome} batch 20261001-090507-0123456"


def test_the_inconclusive_comment_names_the_uv_lock_prs() -> None:
    text = mt.comment("INCONCLUSIVE", "b", reason="Install failed", uv_lock_prs=[7, 9])
    assert "#7, #9" in text and "uv.lock" in text and "build --resume b" in text
    assert "uv.lock" not in mt.comment(
        "INCONCLUSIVE", "b", reason="Install failed", uv_lock_prs=[7]
    )
    with pytest.raises(ValueError):
        mt.comment("APPROVED", "b")


# --- safety review on #479 -------------------------------------------------------------


def test_the_record_check_refuses_a_pr_deleted_from_or_duplicated_in_the_prefix() -> None:
    """A prefix longer than the accepted list, or a PR listed twice, never matches."""
    full = _record([4])
    comments = _tested(full)
    deleted = _record([4])
    del deleted.prs[3]
    assert not mt.record_matches_comments(deleted, comments)
    duplicated = _record([4])
    duplicated.prs[3] = duplicated.prs[0]
    assert not mt.record_matches_comments(duplicated, comments)
    with pytest.raises(ValueError, match="verdicts"):
        mt.mergeable_prefix(deleted, [True] * 4)


def test_the_mergeable_prefix_needs_one_verdict_per_accepted_pr() -> None:
    with pytest.raises(ValueError, match="1 verdicts for 4"):
        mt.mergeable_prefix(_record([4]), [True])


def test_the_culprit_rule_reads_an_exact_head_line() -> None:
    """Check (d) compares the CULPRIT comment's own `head` line, emitted by `comment`,
    for equality: a short SHA or the head quoted elsewhere does not count."""
    batch = "20261001-120000-abcdef1"
    text = mt.comment("CULPRIT", batch, head=HEAD, prefix=2)
    assert f"\nhead {HEAD}\n" in f"{text}\n"
    assert _eligible(comments=(_c(text),)).startswith("(d)")
    short = mt.comment("CULPRIT", batch, head=HEAD[:7])
    assert _eligible(comments=(_c(short),)) is None
    quoted = _c(f"merge-train: CULPRIT batch {batch}\nhead {'b' * 40}\nnot {HEAD}")
    assert _eligible(comments=(quoted,)) is None


def test_a_skipped_claims_run_is_not_green() -> None:
    runs = (
        ready_pr.CheckRun("checks", "COMPLETED", "SUCCESS"),
        ready_pr.CheckRun("claims", "COMPLETED", "SKIPPED"),
    )
    assert _eligible(checks=ready_pr.HeadChecks(HEAD, runs)).startswith("(e)")


@pytest.mark.parametrize(("green", "red"), [(-1, 0), (4, 5), (2, 2), (3, 2)])
def test_bisect_bounds_outside_the_batch_are_refused(green: int, red: int) -> None:
    with pytest.raises(ValueError):
        mt.bisect_result(green, red, [1, 2, 3, 4])


def test_a_green_prefix_outside_the_accepted_list_is_refused() -> None:
    record = _record([3], n=2)
    with pytest.raises(ValueError, match="outside"):
        mt.mergeable_prefix(record, [True, True])
    with pytest.raises(ValueError, match="head SHA"):
        mt.comment("CULPRIT", "b", prefix=2)


# === the runner and `build` (plan T72b) ====================================================


@pytest.fixture(autouse=True)
def _merge_train_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every record/worktree path a test touches lives under `tmp_path`, never the real
    `~/.tradepartner/merge_train/`."""
    monkeypatch.setenv(mt.MERGE_TRAIN_DIR_VAR, str(tmp_path / "merge_train"))


def _run(
    run_id: int = 1,
    attempt: int = 1,
    status: str | None = "completed",
    conclusion: str | None = "success",
    url: str = "u",
    steps: tuple[object, ...] = (),
    duration: float = 900.0,
) -> object:
    return mt.RunInfo(run_id, attempt, status, conclusion, url, steps, duration)


GREEN_MAIN = _run()
GREEN_FULL = _run(steps=(mt.Step("Tests", "success"),))
RED = _run(conclusion="failure", steps=(mt.Step("Hygiene", "failure"),), duration=300.0)
# The confirming rerun GitHub reports at a strictly later attempt than the run it reran
# (SHOULD FIX, safety review of #521): a stale read of the first attempt must never count.
CONFIRM_RED = _run(
    attempt=2, conclusion="failure", steps=(mt.Step("Hygiene", "failure"),), duration=300.0
)
CONFIRM_GREEN = _run(attempt=2, steps=(mt.Step("Tests", "success"),))


class FakeRunner:
    """Scripted git/gh for `build`: no network, no real git. Every tree and commit is a
    short deterministic label (`commit1`, `commit2`, ...), so a test can pre-script the CI
    response for the sha a given accepted PR will land on before `build` computes it."""

    def __init__(self, base: str = "base0", now: datetime | None = None) -> None:
        self.base = base
        self._now = now or datetime(2026, 10, 1, 9, 5, 7, tzinfo=UTC)
        self.pr_registry: dict[int, object] = {}
        self.head_to_number: dict[str, int] = {}
        self.open_numbers: list[int] = []
        self.conflicts: dict[int, set[str]] = {}
        self.ancestors: set[int] = set()
        self.worktrees_added: list[Path] = []
        self.worktrees_removed: list[Path] = []
        self.commits: list[str] = [base]
        self.trees: list[str] = [f"tree:{base}"]
        self.pushed: dict[str, str] = {}
        self.fail_push_branches: set[str] = set()
        self.deleted_branches: list[str] = []
        self.run_script: dict[str, list[object]] = {}
        self.rerun_calls: list[int] = []
        self.comments_posted: list[tuple[int, str]] = []
        self.sleeps = 0
        self.calls: list[str] = []
        self._pending: int | None = None
        # -- merge (plan T72c) --
        self.main_history: list[str] = [base]
        self.tree_for: dict[str, str] = {base: f"tree:{base}"}
        self.parent_for: dict[str, str] = {}
        self.merge_script: dict[int, list[object]] = {}
        self.merge_calls: list[tuple[int, str]] = []
        self.fail_delete_branches: set[str] = set()
        self.landed_tree_override: dict[int, str] = {}
        self.raise_on_post: set[int] = set()
        self.raise_on_pr_data_call: dict[int, int] = {}
        self.pr_data_call_count: dict[int, int] = {}
        # -- prune's `refs/merge-train/*` cleanup (#694) --
        self.merge_train_refs = 0
        self.ref_cleanups = 0
        self.raise_on_unconfirmed_post = False

    def add_pr(
        self,
        number: int,
        *,
        diff_paths: tuple[str, ...] | None = None,
        comments: tuple[object, ...] = (),
        **pr_overrides: object,
    ) -> None:
        head = f"head{number}"
        branch = f"feat/{number}-x"
        fields: dict[str, object] = {
            "number": number,
            "author": OWNER,
            "head_repo": REPO,
            "repo": REPO,
            "state": "OPEN",
            "is_draft": False,
            "base": "main",
            "labels": (),
            "head": head,
            "branch": branch,
            "body": f"Closes #{number}\n\n- [x] done\n",
            "fragment_texts": (FRAGMENT,),
            "title": f"PR {number}",
        }
        fields.update(pr_overrides)
        pr = mt.PullRequest(**fields)
        data = mt.PrData(
            pr,
            comments,
            _checks(sha=head),
            diff_paths or (f"src/mod_{number}.py", f"changelog.d/{number}-x.md"),
        )
        self.pr_registry[number] = data
        self.head_to_number[head] = number
        self.open_numbers.append(number)

    def script_run(self, sha: str, infos: Sequence[object]) -> None:
        self.run_script[sha] = list(infos)

    def script_merge(self, number: int, outcomes: Sequence[object]) -> None:
        self.merge_script[number] = list(outcomes)

    # -- Runner protocol --
    def main_sha(self) -> str:
        return self.main_history[-1]

    def open_pr_numbers(self) -> list[int]:
        return list(self.open_numbers)

    def pr_data(self, number: int) -> object:
        self.pr_data_call_count[number] = self.pr_data_call_count.get(number, 0) + 1
        fail_at = self.raise_on_pr_data_call.get(number)
        if fail_at is not None and self.pr_data_call_count[number] == fail_at:
            raise RuntimeError(f"network error reading #{number}")
        return self.pr_registry[number]

    def add_worktree(self, path: Path, base: str) -> None:
        self.calls.append("add_worktree")
        assert base == self.base
        self.worktrees_added.append(path)

    def remove_worktree(self, path: Path) -> None:
        self.calls.append("remove_worktree")
        self.worktrees_removed.append(path)

    def merge_squash(self, worktree: Path, head: str) -> bool:
        self.calls.append("merge_squash")
        number = self.head_to_number[head]
        self._pending = number
        return number not in self.conflicts

    def commit_squash(self, worktree: Path, message: str) -> str:
        self.calls.append("commit_squash")
        k = len(self.commits)
        sha = f"commit{k}"
        self.commits.append(sha)
        self.trees.append(f"tree{k}")
        return sha

    def abort_merge(self, worktree: Path) -> None:
        self.calls.append("abort_merge")

    def conflicted_paths(self, worktree: Path) -> list[str]:
        self.calls.append("conflicted_paths")
        return sorted(self.conflicts.get(self._pending, set()))

    def is_ancestor(self, maybe_ancestor: str, ref: str) -> bool:
        self.calls.append("is_ancestor")
        return self.head_to_number[maybe_ancestor] in self.ancestors

    def head_sha(self, worktree: Path) -> str:
        return self.commits[-1]

    def tree_of(self, worktree: Path) -> str:
        return self.trees[-1]

    def push(self, worktree: Path, ref: str, branch: str) -> None:
        if branch in self.fail_push_branches:
            raise RuntimeError(f"push to {branch} failed")
        self.pushed[branch] = ref

    def delete_branch(self, branch: str) -> bool:
        # Mirrors `ShellRunner.delete_branch` (#642, follow-up 1): a branch outside
        # `train/*` - e.g. a record hand-edited to name a probe id that was never a real
        # branch - is refused the same way the real runner refuses it.
        if not branch.startswith("train/"):
            raise mt.StoppedError(f"refusing to delete a non-train/ branch: {branch}")
        self.deleted_branches.append(branch)
        return branch not in self.fail_delete_branches

    def tree_of_ref(self, ref: str) -> str:
        sha = self.main_history[-1] if ref in ("origin/main", "HEAD") else ref
        return self.tree_for.get(sha, f"tree:{sha}")

    def parent_of(self, sha: str) -> str:
        return self.parent_for.get(sha, "")

    def merge_pr(self, number: int, head: str) -> object:
        self.merge_calls.append((number, head))
        queue = self.merge_script.get(number)
        attempt = (
            queue.pop(0)
            if queue and len(queue) > 1
            else (queue[0] if queue else mt.MergeAttempt("merged"))
        )
        if attempt.outcome == "merged":
            pos = len(self.main_history)
            new_sha = f"main{pos}"
            self.tree_for[new_sha] = self.landed_tree_override.get(number, f"t{pos}")
            self.parent_for[new_sha] = self.main_history[-1]
            self.main_history.append(new_sha)
        return attempt

    def delete_merge_train_refs(self) -> int:
        self.ref_cleanups += 1
        removed, self.merge_train_refs = self.merge_train_refs, 0
        return removed

    def find_run(self, sha: str, branch: str) -> object:
        queue = self.run_script.get(sha)
        if not queue:
            return mt.RunInfo(None, 0, None, None, None)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def rerun(self, run_id: int) -> None:
        self.rerun_calls.append(run_id)

    def post_comment(self, number: int, text: str) -> None:
        if number in self.raise_on_post and text.startswith("merge-train: MERGED"):
            raise RuntimeError(f"network error posting to #{number}")
        if self.raise_on_unconfirmed_post and text.startswith("merge-train: UNCONFIRMED"):
            raise RuntimeError(f"network error posting to #{number}")
        self.comments_posted.append((number, text))

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1

    def now(self) -> datetime:
        return self._now


def _batch(n: int, fake: FakeRunner | None = None) -> FakeRunner:
    fake = fake or FakeRunner()
    for i in range(1, n + 1):
        fake.add_pr(i)
    return fake


# -- AC6: a green run -------------------------------------------------------------------


def test_build_green_batch_posts_tested_naming_the_full_batch_prefix() -> None:
    fake = _batch(3)
    fake.script_run(fake.base, [GREEN_MAIN])
    fake.script_run("commit3", [GREEN_FULL])
    printed: list[str] = []
    record = mt.run_build(fake, None, None, timeout_s=10, poll_s=0, say=printed.append)
    assert record.outcome == "green"
    assert record.green_prefixes == [3]
    assert {n for n, _ in fake.comments_posted} == {1, 2, 3}
    for _, text in fake.comments_posted:
        assert text.startswith(f"merge-train: TESTED batch {record.batch_id}")
        assert "prefix 3 u" in text
    assert fake.worktrees_added and fake.worktrees_added == fake.worktrees_removed
    # req 8: "the same text is printed and written to the record" - every posted
    # comment's first line is echoed through `say` too.
    assert sum("TESTED batch" in line for line in printed) == 3


# -- AC4: a conflict drops only that PR, and never a merge commit -----------------------


def test_build_drops_a_conflicting_pr_and_the_train_has_no_merge_in_progress() -> None:
    fake = FakeRunner()
    fake.add_pr(1, diff_paths=("a.py", "changelog.d/1-x.md"))
    fake.add_pr(2, diff_paths=("a.py", "changelog.d/2-x.md"))
    fake.add_pr(3, diff_paths=("c.py", "changelog.d/3-x.md"))
    fake.conflicts[2] = {"a.py"}
    fake.script_run(fake.base, [GREEN_MAIN])
    fake.script_run("commit2", [GREEN_FULL])  # PR1 then PR3 accepted: commit1, commit2
    record = mt.run_build(fake, None, None, timeout_s=10, poll_s=0)
    by_number = {p.number: p for p in record.prs}
    assert by_number[2].state == "dropped"
    assert by_number[2].paths == ["a.py"]
    assert by_number[2].conflicts_with == [1]
    assert by_number[1].state == "accepted"
    assert by_number[3].state == "accepted"
    # every commit follows a clean squash merge: the train never makes a merge commit
    for i, name in enumerate(fake.calls):
        if name == "commit_squash":
            assert fake.calls[i - 1] == "merge_squash"
    assert fake.worktrees_added == fake.worktrees_removed
    dropped_text = next(t for n, t in fake.comments_posted if n == 2)
    assert dropped_text.startswith(f"merge-train: DROPPED batch {record.batch_id}")
    assert "a.py" in dropped_text and "#1" in dropped_text


# -- AC5: an already-merged PR is skipped with no comment -------------------------------


def test_build_skips_an_already_merged_pr_with_no_comment() -> None:
    fake = _batch(2)
    fake.ancestors.add(1)
    fake.script_run(fake.base, [GREEN_MAIN])
    fake.script_run("commit1", [GREEN_FULL])  # only PR2 is accepted: commit1
    record = mt.run_build(fake, None, None, timeout_s=10, poll_s=0)
    by_number = {p.number: p for p in record.prs}
    assert by_number[1].state == "already_merged"
    assert by_number[1].reason is None
    assert all(n != 1 for n, _ in fake.comments_posted)
    assert by_number[2].state == "accepted"


# -- AC7: inconclusive, and `--resume` re-attaches to the same branch and run -----------


def test_build_is_inconclusive_on_an_infra_failure_and_resume_reattaches() -> None:
    fake = _batch(2)
    fake.script_run(fake.base, [GREEN_MAIN])
    bad = _run(
        run_id=5,
        conclusion="failure",
        steps=(mt.Step("Install", "failure"), mt.Step("Hygiene", "skipped")),
        duration=20.0,
    )
    fake.script_run("commit2", [bad])
    record = mt.run_build(fake, None, None, timeout_s=10, poll_s=0)
    assert record.outcome == "inconclusive"
    assert not record.green_prefixes
    assert fake.worktrees_added == fake.worktrees_removed

    resumer = FakeRunner()
    resumer.script_run(record.train_sha, [GREEN_FULL])
    resumed = mt.run_build_resume(resumer, record.batch_id, timeout_s=10, poll_s=0)
    assert resumed.outcome == "green"
    assert resumed.green_prefixes == [2]
    assert {n for n, _ in resumer.comments_posted} == {1, 2}


# uv.lock needs the safety review (#382)
LOCK_PASS = (_c("safety-reviewer: PASS"),)


def test_the_inconclusive_comment_names_uv_lock_prs_on_build_and_on_resume() -> None:
    """req 4, AC7: two accepted PRs touching uv.lock are named on an Install failure, and
    the same holds after `--resume` re-attaches (safety review of #521, SHOULD FIX 5)."""
    fake = FakeRunner()
    fake.add_pr(1, diff_paths=("uv.lock", "changelog.d/1-x.md"), comments=LOCK_PASS)
    fake.add_pr(2, diff_paths=("uv.lock", "changelog.d/2-x.md"), comments=LOCK_PASS)
    fake.script_run(fake.base, [GREEN_MAIN])
    install_failed = _run(
        run_id=5,
        conclusion="failure",
        steps=(mt.Step("Install", "failure"), mt.Step("Hygiene", "skipped")),
        duration=20.0,
    )
    fake.script_run("commit2", [install_failed])
    record = mt.run_build(fake, None, None, timeout_s=10, poll_s=0)
    assert record.outcome == "inconclusive"
    text1 = next(t for n, t in fake.comments_posted if n == 1)
    assert "uv.lock" in text1 and "#1, #2" in text1

    resumer = FakeRunner()
    resumer.add_pr(1, diff_paths=("uv.lock", "changelog.d/1-x.md"), comments=LOCK_PASS)
    resumer.add_pr(2, diff_paths=("uv.lock", "changelog.d/2-x.md"), comments=LOCK_PASS)
    resumer.script_run(record.train_sha, [install_failed])
    resumed = mt.run_build_resume(resumer, record.batch_id, timeout_s=10, poll_s=0)
    assert resumed.outcome == "inconclusive"
    resumed_text1 = next(t for n, t in resumer.comments_posted if n == 1)
    assert "uv.lock" in resumed_text1 and "#1, #2" in resumed_text1


# -- AC8: the bisect, both halves ---------------------------------------------------------


def test_build_bisects_to_the_culprit_with_three_probes() -> None:
    fake = _batch(5)
    fake.script_run(fake.base, [GREEN_MAIN])
    fake.script_run("commit5", [RED, CONFIRM_RED])  # the full batch, confirmed red
    fake.script_run("commit2", [GREEN_FULL])  # probe prefix 2
    fake.script_run("commit3", [GREEN_FULL])  # probe prefix 3
    fake.script_run("commit4", [RED, CONFIRM_RED])  # probe prefix 4, confirmed red
    record = mt.run_build(fake, None, None, timeout_s=10, poll_s=0)
    assert [p.k for p in record.probes] == [2, 3, 4]
    assert record.green_prefixes == [2, 3]
    assert record.culprit == 4
    assert record.held == [5]
    culprit_text = next(t for n, t in fake.comments_posted if n == 4)
    assert culprit_text.startswith(f"merge-train: CULPRIT batch {record.batch_id}")
    held_text = next(t for n, t in fake.comments_posted if n == 5)
    assert held_text.startswith(f"merge-train: HELD batch {record.batch_id}")
    assert {n for n, _ in fake.comments_posted if n in (1, 2, 3)} == {1, 2, 3}
    assert fake.worktrees_added == fake.worktrees_removed


def test_build_is_green_with_no_probe_when_the_only_red_run_is_a_flake() -> None:
    fake = _batch(2)
    fake.script_run(fake.base, [GREEN_MAIN])
    fake.script_run("commit2", [RED, CONFIRM_GREEN])  # red once, green on the confirming rerun
    record = mt.run_build(fake, None, None, timeout_s=10, poll_s=0)
    assert record.outcome == "green"
    assert record.probes == []
    assert fake.rerun_calls == [RED.run_id]


# -- AC9: main green at base, all three cases --------------------------------------------


def test_main_red_at_base_stops_before_any_probe() -> None:
    fake = _batch(1)
    fake.script_run(fake.base, [_run(status="completed", conclusion="failure")])
    record = mt.run_build(fake, None, None, timeout_s=10, poll_s=0)
    assert record.main_green_at_base is False
    assert record.outcome == "inconclusive"
    assert record.detail == "main is red at base"
    assert record.probes == []


def test_main_unfinished_at_base_times_out_inconclusive() -> None:
    fake = _batch(1)
    fake.script_run(fake.base, [_run(status="in_progress", conclusion=None)])
    record = mt.run_build(fake, None, None, timeout_s=0, poll_s=0)
    assert record.main_green_at_base is None
    assert record.outcome == "inconclusive"
    assert record.detail == "main unfinished at base"


def test_main_in_progress_then_green_lets_the_batch_proceed() -> None:
    fake = _batch(1)
    fake.script_run(fake.base, [_run(status="in_progress", conclusion=None), GREEN_MAIN])
    fake.script_run("commit1", [GREEN_FULL])
    record = mt.run_build(fake, None, None, timeout_s=10, poll_s=0)
    assert record.main_green_at_base is True
    assert record.outcome == "green"


# -- the worktree: removed on every exit path, and two batches get two ------------------


def test_the_worktree_is_removed_even_when_the_run_raises() -> None:
    fake = _batch(1)
    fake.script_run(fake.base, [GREEN_MAIN])
    base = fake.main_sha()
    bid = mt.batch_id(fake.now(), base)
    fake.fail_push_branches.add(f"train/{bid}")
    with pytest.raises(RuntimeError):
        mt.run_build(fake, None, None, timeout_s=10, poll_s=0)
    assert len(fake.worktrees_added) == 1
    assert fake.worktrees_added == fake.worktrees_removed


def test_two_batches_get_two_worktrees() -> None:
    fake1 = FakeRunner(now=datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC))
    _batch(1, fake1)
    fake1.script_run(fake1.base, [GREEN_MAIN])
    fake1.script_run("commit1", [GREEN_FULL])
    r1 = mt.run_build(fake1, None, None, timeout_s=10, poll_s=0)

    fake2 = FakeRunner(now=datetime(2026, 10, 1, 9, 10, 0, tzinfo=UTC))
    _batch(1, fake2)
    fake2.script_run(fake2.base, [GREEN_MAIN])
    fake2.script_run("commit1", [GREEN_FULL])
    r2 = mt.run_build(fake2, None, None, timeout_s=10, poll_s=0)

    assert r1.batch_id != r2.batch_id
    assert fake1.worktrees_added[0] != fake2.worktrees_added[0]
    assert fake1.worktrees_added == fake1.worktrees_removed
    assert fake2.worktrees_added == fake2.worktrees_removed


# -- safety review of #521 ---------------------------------------------------------------


def test_build_treats_a_fork_pr_as_ineligible_and_never_touches_its_head() -> None:
    """SHOULD FIX 1: a fork PR must not stop, or be built into, the rest of the batch."""
    fake = _batch(2)
    fake.add_pr(3, head_repo="someone/fork")
    fake.script_run(fake.base, [GREEN_MAIN])
    fake.script_run("commit2", [GREEN_FULL])
    record = mt.run_build(fake, None, None, timeout_s=10, poll_s=0)
    by_number = {p.number: p for p in record.prs}
    assert by_number[3].state == "ineligible"
    assert (by_number[3].reason or "").startswith("(0)")
    assert fake.commits == ["base0", "commit1", "commit2"]  # PR3 never entered the train
    assert record.outcome == "green"


def test_post_prints_the_full_comment_text_not_just_its_first_line() -> None:
    """#524, follow-up 4: req 8 says "the same text is printed and written to the record"."""
    fake = FakeRunner()
    printed: list[str] = []
    text = "merge-train: HELD batch b\nreason: x\nmore detail on a later line"
    mt._post(fake, printed.append, 5, text)
    assert "reason: x" in printed[0]
    assert "more detail on a later line" in printed[0]
    assert fake.comments_posted == [(5, text)]


def test_bisect_posts_inconclusive_to_untested_prs_after_an_inconclusive_probe() -> None:
    """#524, follow-up 5: an inconclusive probe (not a confirmed red) stops the bisect with
    no culprit; the PRs beyond the last green prefix get the `INCONCLUSIVE` comment req 8
    names for that case, not just a note in the record's `detail`."""
    fake = _batch(5)
    fake.script_run(fake.base, [GREEN_MAIN])
    fake.script_run("commit5", [RED, CONFIRM_RED])  # the full batch, confirmed red
    fake.script_run("commit2", [GREEN_FULL])  # probe prefix 2: green
    fake.script_run("commit3", [_run(status="in_progress", conclusion=None)])  # probe 3
    record = mt.run_build(fake, None, None, timeout_s=0, poll_s=0)
    assert record.culprit is None
    assert record.held == [3, 4, 5]
    held_texts = {n: t for n, t in fake.comments_posted if n in (3, 4, 5)}
    assert set(held_texts) == {3, 4, 5}
    assert all(t.startswith("merge-train: INCONCLUSIVE") for t in held_texts.values())
    # SHOULD FIX 6 (pass-1 review of #640): `build --resume` refuses a red batch ("nothing
    # to resume"), so this comment must advise a fresh `build`, not `--resume`.
    assert all("build --resume" not in t for t in held_texts.values())
    assert all("rerun `build` for a fresh batch" in t for t in held_texts.values())


def test_shell_runner_find_run_filters_by_workflow_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#524, follow-up 2: a second workflow must never be matched here by accident."""
    runner = mt.ShellRunner(tmp_path)
    captured: list[tuple[str, ...]] = []

    def fake_gh(*args: str) -> str:
        captured.append(args)
        return json.dumps([])

    monkeypatch.setattr(runner, "_gh", fake_gh)
    runner.find_run("sha", "train/x")
    assert captured and "--workflow" in captured[0] and "ci.yml" in captured[0]


class _Proc:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_shell_runner_pr_data_fails_closed_on_a_fetch_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#524, follow-up 3: a push to the PR between reading its metadata and fetching its
    head must fail closed rather than diff against a stale head."""
    runner = mt.ShellRunner(tmp_path)

    def fake_gh(*args: str) -> str:
        if args[:2] == ("repo", "view"):
            return json.dumps({"nameWithOwner": REPO})
        if args[:2] == ("pr", "view"):
            return json.dumps(
                {
                    "number": 7,
                    "author": {"login": OWNER},
                    "isCrossRepository": False,
                    "baseRefName": "main",
                    "state": "OPEN",
                    "isDraft": False,
                    "headRefName": "feat/7-x",
                    "headRefOid": "a" * 40,
                    "body": "",
                    "labels": [],
                    "comments": [],
                    "title": "x",
                }
            )
        raise AssertionError(f"unexpected gh call: {args}")

    private_ref = "refs/merge-train/pr-7"

    def fake_git(cwd: Path, *args: str, check: bool = True) -> _Proc:
        if args[:2] == ("fetch", "-q"):
            # #642, follow-up 5: fetched into a private ref, never the shared `FETCH_HEAD`,
            # which a concurrent `merge_train` run in the same clone could overwrite.
            assert args[-1] == f"+refs/pull/7/head:{private_ref}"
            return _Proc(0, "")
        if args[:2] == ("rev-parse", private_ref):
            return _Proc(0, "b" * 40 + "\n")  # a different sha: the PR moved underneath us
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(runner, "_gh", fake_gh)
    monkeypatch.setattr(runner, "_git", fake_git)
    with pytest.raises(mt.StoppedError, match="stale head"):
        runner.pr_data(7)


def test_classified_never_reads_a_stale_report_of_the_reran_attempt() -> None:
    """SHOULD FIX 2: a confirming rerun must read a later attempt, never the one it reran,
    even when `find_run` still briefly reports the old attempt as `completed`/`failure`."""
    fake = FakeRunner()
    fake.run_script["sha"] = [RED, RED, CONFIRM_GREEN]  # one stale re-read, then the rerun
    outcome, info = mt._classified(
        fake, "sha", "train/x", timeout_s=10, poll_s=0, say=lambda _msg: None
    )
    assert outcome.kind == "green"
    assert info.run_attempt == 2
    assert fake.rerun_calls == [RED.run_id]


def test_shell_runner_pr_data_never_fetches_a_fork_prs_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#524, follow-up 1: a `ShellRunner`-level regression test for the fork-PR ordering fix
    (SHOULD FIX 1, safety review of #521). `gh` is stubbed (no network); `git` is real
    against a local temp repo, so a fetch of the fork PR's head would actually run here if
    the ordering ever regressed."""
    subprocess_run = __import__("subprocess").run
    subprocess_run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = mt.ShellRunner(tmp_path)
    git_calls: list[tuple[str, ...]] = []
    real_git = runner._git

    def spying_git(cwd: Path, *args: str, check: bool = True) -> object:
        git_calls.append(args)
        return real_git(cwd, *args, check=check)

    monkeypatch.setattr(runner, "_git", spying_git)

    def fake_gh(*args: str) -> str:
        if args[:2] == ("repo", "view"):
            return json.dumps({"nameWithOwner": REPO})
        if args[:2] == ("pr", "view"):
            return json.dumps(
                {
                    "number": 42,
                    "author": {"login": "someone-else"},
                    "isCrossRepository": True,
                    "baseRefName": "main",
                    "state": "OPEN",
                    "isDraft": False,
                    "headRefName": "feat/42-x",
                    "headRefOid": "f" * 40,
                    "body": "",
                    "labels": [],
                    "comments": [],
                    "title": "x",
                }
            )
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(runner, "_gh", fake_gh)
    data = runner.pr_data(42)
    assert data.pr.head_repo != data.pr.repo
    assert data.diff_paths == ()
    assert not any(call[:1] == ("fetch",) for call in git_calls)


def test_shell_runner_refuses_to_push_or_delete_a_non_train_branch(tmp_path: Path) -> None:
    """SHOULD FIX 4: the push/delete seam refuses anything outside `train/*`, independent
    of `build` only ever constructing `train/*` names."""
    subprocess_run = __import__("subprocess").run
    subprocess_run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = mt.ShellRunner(tmp_path)
    with pytest.raises(mt.StoppedError):
        runner.push(tmp_path, "HEAD", "main")
    with pytest.raises(mt.StoppedError):
        runner.delete_branch("main")


# === `merge`, `status`, `prune` (plan T72c) ================================================


def _merge_record(fake: FakeRunner, n: int, green: Sequence[int]) -> object:
    """A frozen `Record` matching a `FakeRunner` batch of `n` PRs, with the real `TESTED`
    comments it would carry injected into each PR's fake comments (req 7), and written to
    the (test-private) record directory so `run_merge` can load it."""
    prs = [
        mt.PrEntry(
            number=i,
            head=fake.pr_registry[i].pr.head,
            branch=fake.pr_registry[i].pr.branch,
            issue=i,
            state="accepted",
        )
        for i in range(1, n + 1)
    ]
    record = mt.Record(
        batch_id="20261001-090507-0123456",
        built_at="2026-10-01T09:05:07+00:00",
        base=fake.base,
        main_green_at_base=True,
        requested="all",
        prs=prs,
        trees=[f"t{k}" for k in range(n + 1)],
        train_branch="train/20261001-090507-0123456",
        outcome="green" if list(green) == [n] else "red",
        green_prefixes=list(green),
    )
    for number, texts in _tested(record).items():
        comments = tuple(mt.Comment(author=OWNER, body=t) for t in texts)
        old = fake.pr_registry[number]
        fake.pr_registry[number] = mt.PrData(old.pr, comments, old.head_checks, old.diff_paths)
    mt._save(record)
    return record


def _texts(fake: FakeRunner, outcome: str) -> dict[int, str]:
    return {n: t for n, t in fake.comments_posted if t.startswith(f"merge-train: {outcome}")}


# -- AC10: a green batch merges in order, and nothing merges before verification --------


def test_merge_lands_a_green_batch_in_order_with_match_head_commit() -> None:
    fake = _batch(4)
    record = _merge_record(fake, 4, [4])
    merged = mt.run_merge(fake, record.batch_id)
    assert fake.merge_calls == [(i, f"head{i}") for i in range(1, 5)]
    assert merged.merge is not None and merged.merge.stopped_at_position is None
    assert [m["number"] for m in merged.merge.merged] == [1, 2, 3, 4]
    assert set(_texts(fake, "MERGED")) == {1, 2, 3, 4}
    assert fake.deleted_branches == [record.train_branch]


def test_merge_never_calls_gh_before_the_record_check_passes() -> None:
    fake = _batch(2)
    record = _merge_record(fake, 2, [2])
    old = fake.pr_registry[2]
    fake.pr_registry[2] = mt.PrData(old.pr, (), old.head_checks, old.diff_paths)  # drop TESTED
    with pytest.raises(mt.StoppedError, match="TESTED"):
        mt.run_merge(fake, record.batch_id)
    assert fake.merge_calls == []


# -- AC11: a head changed since build, and `main` no longer at `base` -------------------


def test_merge_shrinks_to_a_green_probe_when_a_head_changed() -> None:
    fake = _batch(4)
    record = _merge_record(fake, 4, [2, 4])
    old = fake.pr_registry[3]
    fake.pr_registry[3] = mt.PrData(
        dataclasses.replace(old.pr, head="newhead3"), old.comments, old.head_checks, old.diff_paths
    )
    merged = mt.run_merge(fake, record.batch_id)
    assert fake.merge_calls == [(1, "head1"), (2, "head2")]
    assert merged.merge is not None and merged.merge.stopped_at_position == 3
    assert set(_texts(fake, "HELD")) == {3, 4}


def test_merge_refuses_when_main_no_longer_equals_base() -> None:
    fake = _batch(2)
    record = _merge_record(fake, 2, [2])
    fake.main_history = ["somewhere-else"]
    with pytest.raises(mt.StoppedError, match="rerun build"):
        mt.run_merge(fake, record.batch_id)
    assert fake.merge_calls == []


# -- AC12: retried, refused on every attempt, and a landed-tree mismatch ----------------


def test_merge_retries_a_not_yet_mergeable_pr_then_succeeds() -> None:
    fake = _batch(2)
    record = _merge_record(fake, 2, [2])
    fake.script_merge(
        2,
        [
            mt.MergeAttempt("retryable", "x"),
            mt.MergeAttempt("retryable", "x"),
            mt.MergeAttempt("merged"),
        ],
    )
    merged = mt.run_merge(fake, record.batch_id)
    assert fake.merge_calls.count((2, "head2")) == 3
    assert merged.merge is not None and merged.merge.stopped_at_position is None
    assert fake.sleeps == 2


def test_merge_stops_when_a_pr_refuses_every_attempt() -> None:
    fake = _batch(4)
    record = _merge_record(fake, 4, [4])
    fake.script_merge(2, [mt.MergeAttempt("retryable", "nope")] * mt.MERGE_RETRY_ATTEMPTS)
    merged = mt.run_merge(fake, record.batch_id)
    assert fake.merge_calls.count((2, "head2")) == mt.MERGE_RETRY_ATTEMPTS
    assert merged.merge is not None and merged.merge.stopped_at_position == 2
    assert {3, 4} <= set(_texts(fake, "HELD"))
    assert 2 not in _texts(fake, "MERGED")
    # SHOULD FIX 2 (pass-1 review of #640): the report names the landed `main` SHA (PR1's)
    # as untested at main's head.
    assert "main1" in (merged.merge.reason or "")
    assert "untested at main's head" in (merged.merge.reason or "")


def test_merge_stops_on_a_landed_tree_mismatch() -> None:
    fake = _batch(4)
    record = _merge_record(fake, 4, [4])
    fake.landed_tree_override[2] = "wrong-tree"
    merged = mt.run_merge(fake, record.batch_id)
    assert merged.merge is not None and merged.merge.stopped_at_position == 3
    # #642, follow-up 7: PR 2's own landing is unconfirmed (its tree does not match), so it
    # never gets a `MERGED` comment - only PR 1's confirmed landing does.
    assert set(_texts(fake, "MERGED")) == {1}
    assert set(_texts(fake, "HELD")) == {3, 4}
    assert "untested at main's head" in (merged.merge.reason or "")


# -- AC13: an edited record refuses before any merge; an unedited red batch's green ------
# -- prefix merges; `merge` takes no PR numbers or order flags --------------------------


def test_merge_refuses_an_edited_record_before_any_merge() -> None:
    fake = _batch(4)
    record = _merge_record(fake, 4, [4])
    edited = mt.Record.from_json(mt.record_path(record.batch_id).read_text())
    edited.prs[0], edited.prs[1] = edited.prs[1], edited.prs[0]
    mt._save(edited)
    with pytest.raises(mt.StoppedError, match="TESTED"):
        mt.run_merge(fake, record.batch_id)
    assert fake.merge_calls == []


def test_merge_accepts_an_unedited_red_batch_with_a_green_prefix() -> None:
    fake = _batch(4)
    record = _merge_record(fake, 4, [2])
    record.outcome, record.culprit, record.held = "red", 3, [4]
    mt._save(record)
    merged = mt.run_merge(fake, record.batch_id)
    assert fake.merge_calls == [(1, "head1"), (2, "head2")]
    assert merged.merge is not None
    # SHOULD FIX 5 (pass-1 review of #640): landing the whole available green prefix of a
    # red batch is complete, not stopped, and `merge` posts no HELD on the culprit or held
    # PRs that `build` already reported (a HELD there would make the culprit eligible again).
    assert merged.merge.stopped_at_position is None
    assert not _texts(fake, "HELD")


def test_merge_argparse_takes_only_the_batch_id_and_resume() -> None:
    parser = mt.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["merge", "20261001-090507-0123456", "5"])
    with pytest.raises(SystemExit):
        parser.parse_args(["merge", "20261001-090507-0123456", "--order", "1", "2"])
    args = parser.parse_args(["merge", "20261001-090507-0123456", "--resume"])
    assert args.batch_id == "20261001-090507-0123456"
    assert args.resume is True


# -- AC17: `merge --resume`, both halves, and a landed-tree mismatch refuses -------------


def test_merge_resume_continues_after_a_stop() -> None:
    fake = _batch(4)
    record = _merge_record(fake, 4, [4])
    fake.script_merge(3, [mt.MergeAttempt("retryable", "x")] * mt.MERGE_RETRY_ATTEMPTS)
    stopped = mt.run_merge(fake, record.batch_id)
    assert stopped.merge is not None and stopped.merge.stopped_at_position == 3
    del fake.merge_script[3]
    resumed = mt.run_merge(fake, record.batch_id, resume=True)
    assert resumed.merge is not None and resumed.merge.stopped_at_position is None
    assert [m["number"] for m in resumed.merge.merged] == [1, 2, 3, 4]


def test_merge_resume_refuses_when_main_moved() -> None:
    fake = _batch(4)
    record = _merge_record(fake, 4, [4])
    fake.script_merge(3, [mt.MergeAttempt("retryable", "x")] * mt.MERGE_RETRY_ATTEMPTS)
    mt.run_merge(fake, record.batch_id)
    fake.main_history.append("somewhere-else")
    with pytest.raises(mt.StoppedError, match="rerun build"):
        mt.run_merge(fake, record.batch_id, resume=True)


def test_merge_resume_refuses_a_landed_tree_mismatch() -> None:
    fake = _batch(4)
    record = _merge_record(fake, 4, [4])
    fake.landed_tree_override[2] = "wrong-tree"
    stopped = mt.run_merge(fake, record.batch_id)
    assert stopped.merge is not None and stopped.merge.stopped_at_position == 3
    with pytest.raises(mt.StoppedError, match="rerun build"):
        mt.run_merge(fake, record.batch_id, resume=True)


# -- `status` and `prune` ----------------------------------------------------------------


def test_status_prints_one_record_or_lists_every_record() -> None:
    fake = _batch(2)
    record = _merge_record(fake, 2, [2])
    printed: list[str] = []
    mt.run_status(record.batch_id, say=printed.append)
    assert record.batch_id in printed[0]
    printed.clear()
    mt.run_status(None, say=printed.append)
    assert any(record.batch_id in line for line in printed)
    with pytest.raises(mt.StoppedError):
        mt.run_status("no-such-batch")


def test_prune_deletes_finished_branches_but_keeps_inconclusive_ones() -> None:
    fake = _batch(2)
    green = _merge_record(fake, 2, [2])
    inconclusive = mt.Record.from_json(green.to_json())
    inconclusive.batch_id = "20261002-000000-0000000"
    inconclusive.outcome = "inconclusive"
    inconclusive.train_branch = "train/20261002-000000-0000000"
    mt._save(inconclusive)
    mt.run_prune(fake)
    assert green.train_branch in fake.deleted_branches
    assert inconclusive.train_branch not in fake.deleted_branches


# === pass-1 fix round on PR #640 (safety-reviewer SHOULD FIX 1-10, NITs) ===================


def _bare_record(
    batch_id: str, outcome: str | None, *, culprit: int | None = None, held: Sequence[int] = ()
) -> object:
    return mt.Record(
        batch_id=batch_id,
        built_at="2026-10-01T09:05:07+00:00",
        base="base0",
        main_green_at_base=True,
        requested="all",
        prs=[],
        trees=["t0"],
        train_branch=f"train/{batch_id}",
        outcome=outcome,
        culprit=culprit,
        held=list(held),
    )


def test_merge_resume_floors_held_start_and_never_moves_stop_backwards() -> None:
    """SHOULD FIX 1: a resume whose target shrinks below `start` must never post HELD on an
    already-merged PR, or move `stopped_at_position` backwards."""
    fake = _batch(4)
    record = _merge_record(fake, 4, [4])
    fake.script_merge(2, [mt.MergeAttempt("retryable", "nope")] * mt.MERGE_RETRY_ATTEMPTS)
    stopped = mt.run_merge(fake, record.batch_id)
    assert stopped.merge is not None and stopped.merge.stopped_at_position == 2

    old = fake.pr_registry[2]
    fake.pr_registry[2] = mt.PrData(
        dataclasses.replace(old.pr, head="still-broken"),
        old.comments,
        old.head_checks,
        old.diff_paths,
    )
    resumed = mt.run_merge(fake, record.batch_id, resume=True)
    assert resumed.merge is not None and resumed.merge.stopped_at_position == 2
    assert 1 not in _texts(fake, "HELD")


def test_merge_stops_on_a_tree_mismatch_even_on_the_last_pr() -> None:
    """SHOULD FIX 3: a parent/tree failure on the LAST PR must stop the merge and name the
    untested SHA, not report it as complete."""
    fake = _batch(2)
    record = _merge_record(fake, 2, [2])
    fake.landed_tree_override[2] = "wrong-tree"
    merged = mt.run_merge(fake, record.batch_id)
    assert merged.merge is not None and merged.merge.stopped_at_position is not None
    assert "untested at main's head" in (merged.merge.reason or "")


class _HeadChangingRunner(FakeRunner):
    """PR2's head changes, in the fake's own PR data, the instant PR1 lands - simulating a
    push to PR2 that arrives during PR1's merge/retry window."""

    def merge_pr(self, number: int, head: str) -> object:
        attempt = super().merge_pr(number, head)
        if number == 1 and attempt.outcome == "merged":
            old = self.pr_registry[2]
            self.pr_registry[2] = mt.PrData(
                dataclasses.replace(old.pr, head="changed-after-pr1"),
                old.comments,
                old.head_checks,
                old.diff_paths,
            )
        return attempt


def test_merge_rereads_the_head_live_immediately_before_merging() -> None:
    """SHOULD FIX 4: the snapshot taken before the loop starts is already stale by the time
    PR2's turn comes; the live re-read must catch a head that changed after PR1 merged."""
    fake = _batch(4, _HeadChangingRunner())
    record = _merge_record(fake, 4, [4])
    merged = mt.run_merge(fake, record.batch_id)
    assert fake.merge_calls == [(1, "head1")]
    assert merged.merge is not None and merged.merge.stopped_at_position == 2
    assert "head" in (merged.merge.reason or "").lower()
    # pass-2 review of #640, SHOULD FIX 1: the reason names PR 1's landed SHA as untested.
    assert "main1" in (merged.merge.reason or "")


class _ParentBreakingRunner(FakeRunner):
    break_after: int = 0

    def merge_pr(self, number: int, head: str) -> object:
        attempt = super().merge_pr(number, head)
        if number == self.break_after and attempt.outcome == "merged":
            self.parent_for[self.main_history[-1]] = "someone-elses-commit"
        return attempt


def test_merge_names_which_check_failed_parent_or_tree() -> None:
    """NIT (pass-1 review of #640): a parent mismatch (e.g. a concurrent hand merge) must
    be named distinctly from a tree mismatch."""
    runner = _ParentBreakingRunner()
    runner.break_after = 2
    fake = _batch(2, runner)
    record = _merge_record(fake, 2, [2])
    merged = mt.run_merge(fake, record.batch_id)
    assert merged.merge is not None
    assert "parent" in (merged.merge.reason or "")


def test_merge_reports_a_failed_branch_deletion_without_raising() -> None:
    """NIT (pass-1 review of #640): `FakeRunner.fail_delete_branches` is exercised - a
    failed deletion is reported, not fatal."""
    fake = _batch(2)
    record = _merge_record(fake, 2, [2])
    fake.fail_delete_branches.add(record.train_branch)
    printed: list[str] = []
    merged = mt.run_merge(fake, record.batch_id, say=printed.append)
    assert merged.merge is not None and merged.merge.stopped_at_position is None
    assert any("remove it by hand" in line for line in printed)


def test_merge_refuses_when_another_merge_holds_the_lock() -> None:
    """SHOULD FIX 7: two concurrent `merge` runs on this machine must never interleave."""
    fake = _batch(2)
    record = _merge_record(fake, 2, [2])
    lock_path = mt.record_dir() / "merge.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(mt.StoppedError, match="another merge"):
            mt.run_merge(fake, record.batch_id)
        assert fake.merge_calls == []


def test_prune_skips_in_flight_and_unfinished_bisect_records() -> None:
    """SHOULD FIX 8: a build still running (`outcome` `None`), or a red run whose bisect
    hasn't concluded (no culprit, no held), must be left alone; a batch already pruned is
    not retried."""
    fake = FakeRunner()
    in_flight = _bare_record("20261003-000000-0000000", None)
    unfinished_red = _bare_record("20261003-000001-0000000", "red")
    finished_red = _bare_record("20261003-000002-0000000", "red", culprit=9)
    green = _bare_record("20261003-000003-0000000", "green")
    for rec in (in_flight, unfinished_red, finished_red, green):
        mt._save(rec)
    (mt.record_dir() / f"worktree-{in_flight.batch_id}").mkdir(parents=True, exist_ok=True)
    (mt.record_dir() / f"worktree-{finished_red.batch_id}").mkdir(parents=True, exist_ok=True)

    mt.run_prune(fake)
    assert in_flight.train_branch not in fake.deleted_branches
    assert unfinished_red.train_branch not in fake.deleted_branches
    assert finished_red.train_branch in fake.deleted_branches
    assert green.train_branch in fake.deleted_branches
    assert mt.worktree_path(in_flight.batch_id) not in fake.worktrees_removed
    assert mt.worktree_path(finished_red.batch_id) in fake.worktrees_removed

    fake.deleted_branches.clear()
    mt.run_prune(fake)
    assert fake.deleted_branches == []  # already pruned; not retried


# === pass-2 fix round on PR #640 (safety-reviewer verification, SHOULD FIX 1) ==============


def test_merge_exception_from_the_merged_post_after_landing_records_the_stop() -> None:
    """Pass-2 review: an exception raised while posting PR 2's own `MERGED` comment, after
    it already landed, must still record a stop position derived from what actually landed
    (`len(merged) + 1 == 3`) and name PR 2's SHA as untested."""
    fake = _batch(4)
    record = _merge_record(fake, 4, [4])
    fake.raise_on_post.add(2)
    with pytest.raises(RuntimeError):
        mt.run_merge(fake, record.batch_id)
    saved = mt.Record.from_json(mt.record_path(record.batch_id).read_text())
    assert saved.merge is not None and saved.merge.stopped_at_position == 3
    assert "main2" in (saved.merge.reason or "")
    assert "untested at main's head" in (saved.merge.reason or "")


def test_merge_exception_from_pr_data_before_landing_does_not_skip_the_pr() -> None:
    """Pass-2 review: an exception from the live `pr_data` re-read, before PR 2 even
    attempts to merge, must record `stopped_at_position == 2` - not `3` - so `--resume`
    retries PR 2 rather than skipping a PR that never landed."""
    fake = _batch(4)
    record = _merge_record(fake, 4, [4])
    fake.raise_on_pr_data_call[2] = 2  # 1st call: the req-7 snapshot; 2nd: the live re-read
    with pytest.raises(RuntimeError):
        mt.run_merge(fake, record.batch_id)
    saved = mt.Record.from_json(mt.record_path(record.batch_id).read_text())
    assert saved.merge is not None and saved.merge.stopped_at_position == 2
    assert "main1" in (saved.merge.reason or "")


def test_merge_resume_after_an_exception_posts_no_held_on_merged_prs() -> None:
    """Pass-2 review: resuming after an exception-caused stop must not post HELD on the
    PRs that had already, verifiably, landed."""
    fake = _batch(4)
    record = _merge_record(fake, 4, [4])
    fake.raise_on_pr_data_call[2] = 2
    with pytest.raises(RuntimeError):
        mt.run_merge(fake, record.batch_id)
    del fake.raise_on_pr_data_call[2]
    resumed = mt.run_merge(fake, record.batch_id, resume=True)
    assert resumed.merge is not None and resumed.merge.stopped_at_position is None
    assert 1 not in _texts(fake, "HELD")


# === follow-up fixes from the #640 safety/quant reviews (#642) ============================


def test_merge_continues_after_an_edited_probe_branch_refuses_deletion() -> None:
    """Follow-up 1: a record hand-edited to name a non-`train/` branch (e.g. a pasted probe
    id) must not turn a successful merge into exit 1 - the refusal is reported, not fatal."""
    fake = _batch(2)
    record = _merge_record(fake, 2, [2])
    record.probes.append(
        mt.Probe(
            k=1,
            branch="not-a-train-branch",
            sha="x",
            run_id=None,
            run_attempt=None,
            run_url=None,
            outcome="green",
        )
    )
    mt._save(record)
    printed: list[str] = []
    merged = mt.run_merge(fake, record.batch_id, say=printed.append)
    assert merged.merge is not None and merged.merge.stopped_at_position is None
    assert any("not-a-train-branch" in line for line in printed)
    assert "not-a-train-branch" not in fake.deleted_branches


def test_merge_skips_pr_data_for_dropped_and_already_merged_prs() -> None:
    """Follow-up 2: `merge` must not read live `pr_data` for a PR outside the accepted list
    - a push to a dropped PR during `merge` must never abort the whole batch."""
    fake = _batch(2)
    record = _merge_record(fake, 2, [2])
    record.prs.append(
        mt.PrEntry(
            number=99,
            head="deadbeef",
            branch="feat/99-x",
            issue=99,
            state="dropped",
            reason="conflict",
        )
    )
    mt._save(record)
    fake.raise_on_pr_data_call[99] = 1
    merged = mt.run_merge(fake, record.batch_id)
    assert merged.merge is not None and merged.merge.stopped_at_position is None
    assert fake.pr_data_call_count.get(99, 0) == 0


def test_shell_runner_merge_pr_treats_a_timeout_as_failed_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Follow-up 3: a `gh pr merge` that times out is an ambiguous outcome (fail closed),
    never `merged`, and the call carries an explicit timeout."""
    runner = mt.ShellRunner(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured["timeout"] = kwargs.get("timeout")
        raise mt.subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout") or 0)

    monkeypatch.setattr(mt.subprocess, "run", fake_run)
    attempt = runner.merge_pr(5, "abc123")
    assert attempt.outcome != "merged"
    assert captured["timeout"] is not None


def test_shell_runner_merge_pr_treats_a_conflict_as_non_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Follow-up 4: GitHub's permanent refusals (a real conflict, a policy block) also say
    "not mergeable", so they must stop the merge at once, not retry it for ~2 minutes as if
    it were the transient "mergeable state unknown" race."""
    runner = mt.ShellRunner(tmp_path)

    class _Result:
        returncode = 1
        stdout = ""
        stderr = (
            "GraphQL: Pull Request is not mergeable: the merge commit cannot be cleanly "
            "created. (mergePullRequest)"
        )

    monkeypatch.setattr(mt.subprocess, "run", lambda *a, **k: _Result())
    attempt = runner.merge_pr(5, "abc123")
    assert attempt.outcome == "failed"


def test_shell_runner_merge_pr_still_retries_when_mergeable_state_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Follow-up 4, the other side: the genuinely transient race is still retryable."""
    runner = mt.ShellRunner(tmp_path)

    class _Result:
        returncode = 1
        stdout = ""
        stderr = (
            "GraphQL: Pull Request is not mergeable: the mergeable state is unknown. "
            "Please check back later. (mergePullRequest)"
        )

    monkeypatch.setattr(mt.subprocess, "run", lambda *a, **k: _Result())
    attempt = runner.merge_pr(5, "abc123")
    assert attempt.outcome == "retryable"


def test_shell_runner_merge_pr_retries_a_plain_not_yet_mergeable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Code-review follow-up on #693, item 1: a plain "not mergeable", with none of the
    permanent-refusal wording, is GitHub still computing mergeability - exactly what the
    train hits by design right after the previous PR lands - so it must stay retryable."""
    runner = mt.ShellRunner(tmp_path)

    class _Result:
        returncode = 1
        stdout = ""
        stderr = "GraphQL: Pull Request is not mergeable (mergePullRequest)"

    monkeypatch.setattr(mt.subprocess, "run", lambda *a, **k: _Result())
    attempt = runner.merge_pr(5, "abc123")
    assert attempt.outcome == "retryable"


def test_prune_never_removes_a_worktree_with_no_record_yet() -> None:
    """Follow-up 6: `build` creates the worktree before it can save the batch's first
    record; a concurrent `prune` must not guess an unrecorded worktree is orphaned."""
    fake = FakeRunner()
    (mt.record_dir() / "worktree-20261003-000000-0000000").mkdir(parents=True, exist_ok=True)
    printed: list[str] = []
    mt.run_prune(fake, say=printed.append)
    assert mt.worktree_path("20261003-000000-0000000") not in fake.worktrees_removed
    # code-review follow-up on #693, item 3: the skip is visible, not silent.
    assert any("no batch record" in line for line in printed)


def test_prune_reports_a_failed_branch_deletion_without_raising() -> None:
    """Code-review follow-up on #693, item 2: a record hand-edited to name a branch outside
    `train/*` must not stop `prune` from moving on to the next branch or the next record,
    the same as follow-up 1 does for `merge`."""
    fake = _batch(2)
    record = _merge_record(fake, 2, [2])
    record.probes.append(
        mt.Probe(
            k=1,
            branch="not-a-train-branch",
            sha="x",
            run_id=None,
            run_attempt=None,
            run_url=None,
            outcome="green",
        )
    )
    mt._save(record)
    printed: list[str] = []
    mt.run_prune(fake, say=printed.append)
    assert any("not-a-train-branch" in line for line in printed)
    saved = mt.Record.from_json(mt.record_path(record.batch_id).read_text())
    assert saved.pruned is False
    assert record.train_branch in fake.deleted_branches


def test_prune_refuses_when_a_merge_holds_the_lock() -> None:
    """Follow-up 8: `prune` must take the same lock `merge` uses before touching any record,
    so the two can never interleave."""
    fake = _batch(2)
    record = _merge_record(fake, 2, [2])
    lock_path = mt.record_dir() / "merge.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(mt.StoppedError, match="another merge"):
            mt.run_prune(fake)
    assert record.batch_id  # the record is untouched; nothing to assert beyond the refusal


def test_merge_exception_before_merging_names_what_actually_happened() -> None:
    """Follow-up 9: an exception from the live `pr_data` re-read, before any merge attempt
    for that PR, must not claim a merge happened."""
    fake = _batch(4)
    record = _merge_record(fake, 4, [4])
    fake.raise_on_pr_data_call[2] = 2  # 1st call: the req-7 snapshot; 2nd: the live re-read
    with pytest.raises(RuntimeError):
        mt.run_merge(fake, record.batch_id)
    saved = mt.Record.from_json(mt.record_path(record.batch_id).read_text())
    assert saved.merge is not None
    assert "after gh pr merge" not in (saved.merge.reason or "")
    assert "before attempting to merge" in (saved.merge.reason or "")


def test_merge_keeps_the_first_stop_reason_when_a_second_stop_follows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Follow-up 10: if persisting the first stop's reason itself raises, and the exception
    handler then calls `_stop` again, the second call must not replace the first reason."""
    fake = _batch(4, _HeadChangingRunner())
    record = _merge_record(fake, 4, [4])
    real_save = mt._save
    calls = {"n": 0}

    def flaky_save(rec: object) -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("disk full")
        real_save(rec)

    monkeypatch.setattr(mt, "_save", flaky_save)
    with pytest.raises(RuntimeError, match="disk full"):
        mt.run_merge(fake, record.batch_id)
    saved = mt.Record.from_json(mt.record_path(record.batch_id).read_text())
    assert saved.merge is not None
    assert "head or eligibility changed" in (saved.merge.reason or "")
    assert "could not verify" not in (saved.merge.reason or "")


# === #694: follow-ups from the #642 safety review ==========================================


def test_merge_posts_unconfirmed_on_a_landed_tree_mismatch() -> None:
    """#694: a PR whose code landed but failed the tree/parent check gets its own
    `UNCONFIRMED` comment (never `MERGED`), naming the landed SHA and which check failed."""
    fake = _batch(4)
    record = _merge_record(fake, 4, [4])
    fake.landed_tree_override[2] = "wrong-tree"
    merged = mt.run_merge(fake, record.batch_id)
    unconfirmed = _texts(fake, "UNCONFIRMED")
    assert set(unconfirmed) == {2}
    assert unconfirmed[2].splitlines()[0] == f"merge-train: UNCONFIRMED batch {record.batch_id}"
    assert "main_sha: main2" in unconfirmed[2]
    assert "landed tree for #2 does not match tree_2" in unconfirmed[2]
    assert set(_texts(fake, "MERGED")) == {1}
    assert set(_texts(fake, "HELD")) == {3, 4}
    assert merged.merge is not None and merged.merge.stopped_at_position == 3


def test_merge_still_stops_cleanly_when_the_unconfirmed_post_fails() -> None:
    """#694: a failed `UNCONFIRMED` post is reported, never turned into an exception that
    skips the `HELD` comments on the rest of the batch."""
    fake = _batch(4)
    record = _merge_record(fake, 4, [4])
    fake.landed_tree_override[2] = "wrong-tree"
    fake.raise_on_unconfirmed_post = True
    printed: list[str] = []
    merged = mt.run_merge(fake, record.batch_id, say=printed.append)
    assert any("could not post UNCONFIRMED on #2" in line for line in printed)
    assert set(_texts(fake, "HELD")) == {3, 4}
    assert merged.merge is not None and merged.merge.stopped_at_position == 3
    assert "does not match tree_2" in (merged.merge.reason or "")


def test_merge_reason_after_a_failed_merged_post_says_the_landing_was_verified() -> None:
    """#694: when only posting `MERGED` fails, after the landing was verified and recorded,
    the reason must not claim the landing is unverified, and `--resume` goes on from the
    next PR."""
    fake = _batch(4)
    record = _merge_record(fake, 4, [4])
    fake.raise_on_post.add(2)
    with pytest.raises(RuntimeError):
        mt.run_merge(fake, record.batch_id)
    saved = mt.Record.from_json(mt.record_path(record.batch_id).read_text())
    assert saved.merge is not None
    reason = saved.merge.reason or ""
    assert "could not verify" not in reason
    assert "#2 landed and was verified" in reason
    assert "--resume" in reason

    fake.raise_on_post.clear()
    resumed = mt.run_merge(fake, record.batch_id, resume=True)
    assert [m["number"] for m in resumed.merge.merged] == [1, 2, 3, 4]
    assert fake.merge_calls.count((2, "head2")) == 1


@pytest.mark.parametrize(
    "stderr",
    [
        "GraphQL: Pull Request is still a draft (mergePullRequest)",
        "GraphQL: Pull Request is not mergeable: changes must be made through the merge "
        "queue (mergePullRequest)",
    ],
)
def test_shell_runner_merge_pr_stops_at_once_on_a_draft_or_merge_queue_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stderr: str
) -> None:
    """#694: a draft PR or a merge-queue requirement never clears by waiting, so it is
    `failed` at once, not retried for about two minutes."""
    runner = mt.ShellRunner(tmp_path)

    class _Result:
        returncode = 1
        stdout = ""

    _Result.stderr = stderr  # type: ignore[attr-defined]
    monkeypatch.setattr(mt.subprocess, "run", lambda *a, **k: _Result())
    assert runner.merge_pr(5, "abc123").outcome == "failed"


def test_prune_deletes_merge_train_refs_when_no_build_is_in_flight() -> None:
    fake = _batch(2)
    _merge_record(fake, 2, [2])
    fake.merge_train_refs = 3
    printed: list[str] = []
    mt.run_prune(fake, say=printed.append)
    assert fake.ref_cleanups == 1
    assert any("deleted 3 refs/merge-train/*" in line for line in printed)
    assert not any("nothing to prune" in line for line in printed)


def test_prune_keeps_merge_train_refs_while_a_build_is_in_flight() -> None:
    """#694: an unfinished record, or a worktree with no record yet, may be a build still
    reading PRs into those refs: they are left alone."""
    fake = FakeRunner()
    mt._save(_bare_record("20261003-000000-0000000", None))
    fake.merge_train_refs = 2
    mt.run_prune(fake)
    assert fake.ref_cleanups == 0

    mt.record_path("20261003-000000-0000000").unlink()
    (mt.record_dir() / "worktree-20261003-000001-0000000").mkdir(parents=True)
    mt.run_prune(fake)
    assert fake.ref_cleanups == 0


def test_prune_says_nothing_to_prune_on_an_empty_record_directory() -> None:
    """#694: `_merge_lock` creates the record directory first, so "nothing to prune" must
    come from what prune found, not from a missing directory."""
    fake = FakeRunner()
    printed: list[str] = []
    mt.run_prune(fake, say=printed.append)
    assert printed == ["nothing to prune"]
    assert fake.ref_cleanups == 1


def _git_repo(path: Path) -> None:
    run = __import__("subprocess").run
    run(["git", "init", "-q", str(path)], check=True)
    run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
    )


def test_shell_runner_deletes_only_merge_train_refs(tmp_path: Path) -> None:
    """#694: real git against a local temp repo: `refs/merge-train/*` go, nothing else."""
    run = __import__("subprocess").run
    _git_repo(tmp_path)
    for ref in ("refs/merge-train/pr-1", "refs/merge-train/pr-2", "refs/heads/keep"):
        run(["git", "-C", str(tmp_path), "update-ref", ref, "HEAD"], check=True)
    runner = mt.ShellRunner(tmp_path)
    assert runner.delete_merge_train_refs() == 2
    left = run(
        ["git", "-C", str(tmp_path), "for-each-ref", "--format=%(refname)"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert "refs/heads/keep" in left
    assert not any(ref.startswith("refs/merge-train/") for ref in left)
    assert runner.delete_merge_train_refs() == 0


def test_shell_runner_pr_data_fails_closed_when_its_ref_vanishes_after_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#694: `prune` now deletes `refs/merge-train/*`; a ref gone right after a successful
    fetch must stop, never silently skip the stale-head check."""
    runner = mt.ShellRunner(tmp_path)

    def fake_gh(*args: str) -> str:
        if args[:2] == ("repo", "view"):
            return json.dumps({"nameWithOwner": REPO})
        return json.dumps(
            {
                "number": 7,
                "author": {"login": OWNER},
                "isCrossRepository": False,
                "baseRefName": "main",
                "state": "OPEN",
                "isDraft": False,
                "headRefName": "feat/7-x",
                "headRefOid": "a" * 40,
                "body": "",
                "labels": [],
                "comments": [],
                "title": "x",
            }
        )

    def fake_git(cwd: Path, *args: str, check: bool = True) -> _Proc:
        if args[:2] == ("fetch", "-q"):
            return _Proc(0, "")
        if args[:1] == ("rev-parse",):
            return _Proc(128, "")  # the ref is gone
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(runner, "_gh", fake_gh)
    monkeypatch.setattr(runner, "_git", fake_git)
    with pytest.raises(mt.StoppedError, match="vanished"):
        runner.pr_data(7)

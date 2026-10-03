"""Merge train, pure core (plan T72; spec docs/specs/merge-train.md reqs 1, 2, 4 to 8).

No network, no git: every function under test is pure. AC numbers are the spec's.
"""

from __future__ import annotations

import importlib.util
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

    def add_pr(
        self, number: int, *, diff_paths: tuple[str, ...] | None = None, **pr_overrides: object
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
            (),
            _checks(sha=head),
            diff_paths or (f"src/mod_{number}.py", f"changelog.d/{number}-x.md"),
        )
        self.pr_registry[number] = data
        self.head_to_number[head] = number
        self.open_numbers.append(number)

    def script_run(self, sha: str, infos: Sequence[object]) -> None:
        self.run_script[sha] = list(infos)

    # -- Runner protocol --
    def main_sha(self) -> str:
        return self.base

    def open_pr_numbers(self) -> list[int]:
        return list(self.open_numbers)

    def pr_data(self, number: int) -> object:
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

    def delete_branch(self, branch: str) -> None:
        self.deleted_branches.append(branch)

    def find_run(self, sha: str, branch: str) -> object:
        queue = self.run_script.get(sha)
        if not queue:
            return mt.RunInfo(None, 0, None, None, None)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def rerun(self, run_id: int) -> None:
        self.rerun_calls.append(run_id)

    def post_comment(self, number: int, text: str) -> None:
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


def test_the_inconclusive_comment_names_uv_lock_prs_on_build_and_on_resume() -> None:
    """req 4, AC7: two accepted PRs touching uv.lock are named on an Install failure, and
    the same holds after `--resume` re-attaches (safety review of #521, SHOULD FIX 5)."""
    fake = FakeRunner()
    fake.add_pr(1, diff_paths=("uv.lock", "changelog.d/1-x.md"))
    fake.add_pr(2, diff_paths=("uv.lock", "changelog.d/2-x.md"))
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
    resumer.add_pr(1, diff_paths=("uv.lock", "changelog.d/1-x.md"))
    resumer.add_pr(2, diff_paths=("uv.lock", "changelog.d/2-x.md"))
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

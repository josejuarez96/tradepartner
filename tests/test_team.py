"""Tests for scripts/team.py: plan parsing, readiness, claim resolution and the CI guard.

Only the pure functions and the command logic behind a fake GitHub are tested here.
The real ``gh`` wrapper is a thin shell and is exercised by using the tool.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "team", Path(__file__).resolve().parents[1] / "scripts" / "team.py"
)
assert _SPEC is not None and _SPEC.loader is not None
team = importlib.util.module_from_spec(_SPEC)
sys.modules["team"] = team  # dataclasses resolve annotations via sys.modules
_SPEC.loader.exec_module(team)

PLAN = """# Plan: Data foundation (Phase 2)

## Tasks
- [x] **T1: Config, dependencies, calendar.** Files: `a.py` · Depends on: n/a · Review: qa.
- [ ] **T3 (owner): Record fixtures.** Files: `x` · Depends on: T1 · Review: safety-reviewer.
- [x] **T4: Store schema.** Files: `s.py` · Depends on: T1 · Review: quant-auditor.
- [ ] **T5: Fixture universe (CSV) and generator.** Files: `f.py` · Depends on: T4 · Review: qa.
- [ ] **T8b: Delistings.** Files: `d.py` · Depends on: T5 · Review: quant-auditor.
- [ ] **T10: Suite.** Files: `u.py` · Depends on: T5, T8b · Review: quant-auditor.
- [ ] **T20: `Broker` interface and fake broker.** Files: `b.py` · Depends on: T4 · Review: sr.
"""


class FakeGitHub:
    """In-memory GitHub that records the calls the commands make."""

    def __init__(self) -> None:
        self.issues: dict[int, team.Issue] = {}
        self.comments: dict[int, list[str]] = {}
        self.prs: list[team.PullRequest] = []
        self.labels: set[str] = set()
        self.closed: list[int] = []
        self.next_number = 100
        self.on_create: list[int] = []  # issues that "appear" concurrently on create

    def list_issues(self, label: str | None = None) -> list[team.Issue]:
        out = [i for i in self.issues.values() if i.state == "OPEN"]
        return [i for i in out if label is None or label in i.labels]

    def get_issue(self, number: int) -> team.Issue:
        return self.issues[number]

    def issue_comments(self, number: int) -> list[str]:
        return list(self.comments.get(number, []))

    def create_issue(self, title: str, body: str, labels: Sequence[str]) -> int:
        for n in self.on_create:  # simulate another team creating just before us
            self.issues[n] = team.Issue(n, title, tuple(labels))
        self.on_create = []
        n = self.next_number
        self.next_number += 1
        self.issues[n] = team.Issue(n, title, tuple(labels))
        return n

    def close_issue(self, number: int, comment: str) -> None:
        i = self.issues[number]
        self.issues[number] = team.Issue(i.number, i.title, i.labels, "CLOSED")
        self.closed.append(number)

    def comment(self, number: int, body: str) -> None:
        self.comments.setdefault(number, []).append(body)

    def add_labels(self, number: int, labels: Sequence[str]) -> None:
        i = self.issues[number]
        merged = tuple(dict.fromkeys([*i.labels, *labels]))
        self.issues[number] = team.Issue(i.number, i.title, merged, i.state)

    def remove_labels(self, number: int, labels: Sequence[str]) -> None:
        i = self.issues[number]
        kept = tuple(lb for lb in i.labels if lb not in labels)
        self.issues[number] = team.Issue(i.number, i.title, kept, i.state)

    def ensure_label(self, name: str, color: str, description: str) -> None:
        self.labels.add(name)

    def list_prs(self) -> list[team.PullRequest]:
        return list(self.prs)

    def get_pr(self, number: int) -> team.PullRequest:
        return next(p for p in self.prs if p.number == number)

    def _edit_pr(self, number: int, add: Sequence[str] = (), remove: Sequence[str] = ()) -> None:
        for k, p in enumerate(self.prs):
            if p.number == number:
                labels = tuple(lb for lb in dict.fromkeys([*p.labels, *add]) if lb not in remove)
                self.prs[k] = team.PullRequest(p.number, p.branch, p.draft, labels, p.title)

    def pr_add_labels(self, number: int, labels: Sequence[str]) -> None:
        self._edit_pr(number, add=labels)

    def pr_remove_labels(self, number: int, labels: Sequence[str]) -> None:
        self._edit_pr(number, remove=labels)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "data-foundation.md").write_text(PLAN)
    (tmp_path / team.TEAM_FILE).write_text("atlas\n")
    return tmp_path


def _pr(n: int, branch: str, labels: tuple[str, ...] = ()) -> team.PullRequest:
    return team.PullRequest(n, branch, True, labels, f"pr {n}")


# ── pure logic ──────────────────────────────────────────────────────────────────


def test_parse_plan_reads_ids_titles_deps_owner_and_phase() -> None:
    tasks = {t.id: t for t in team.parse_plan(PLAN, "docs/plans/data-foundation.md")}
    assert set(tasks) == {"T1", "T3", "T4", "T5", "T8b", "T10", "T20"}
    assert tasks["T5"].title == "Fixture universe (CSV) and generator"
    assert tasks["T20"].title == "`Broker` interface and fake broker"
    assert tasks["T10"].depends_on == ("T5", "T8b")
    assert tasks["T1"].depends_on == ()
    assert tasks["T3"].owner and not tasks["T5"].owner
    assert tasks["T1"].done and not tasks["T5"].done
    assert tasks["T5"].phase == 2


def test_ready_frontier_requires_every_dependency_ticked() -> None:
    tasks = team.parse_plan(PLAN, "p")
    assert [t.id for t in team.ready_tasks(tasks)] == ["T3", "T5", "T20"]


def test_resolve_holder_whole_body_claims_in_order_with_release() -> None:
    assert team.resolve_holder([]) is None
    assert team.resolve_holder(["claim: team:a", "claim: team:b"]) == "a"
    assert team.resolve_holder(["claim: team:a", "release: team:a", "claim: team:b"]) == "b"
    assert team.resolve_holder(["claim: team:a", "release: team:b"]) == "a"
    assert team.resolve_holder(["claim: team:a\n"]) == "a"
    # A quoted line inside a handoff note never claims or releases.
    assert team.resolve_holder(["Looks good!\nclaim: team:a\nthanks"]) is None
    assert team.resolve_holder(["claim: team:a", "handoff: I ran `release: team:a` locally"]) == "a"
    assert team.resolve_holder(["claim: team:Not Valid"]) is None


def test_canonical_issue_is_lowest_open_number() -> None:
    issues = [
        team.Issue(25, "T5", ("task:T5",)),
        team.Issue(22, "T5", ("task:T5",)),
        team.Issue(9, "T5", ("task:T5",), "CLOSED"),
    ]
    winner = team.canonical_issue(issues)
    assert winner is not None and winner.number == 22
    assert team.canonical_issue([]) is None


def test_branch_issue_number_title_task_and_suggestion() -> None:
    assert team.issue_number_from_branch("feat/22-fixture-universe") == 22
    assert team.issue_number_from_branch("main") is None
    assert team.task_from_title("T5: fixture universe (CSV) and generator") == "T5"
    assert team.task_from_title("T3 (owner): record fixtures") == "T3"
    assert team.task_from_title("T8b: delistings") == "T8b"
    assert team.task_from_title("process: T5 follow-up") is None
    issue = team.Issue(36, "process: multi-team orchestration (claims)", ("type:docs",))
    assert team.suggest_branch(issue) == "docs/36-process-multi-team-orchestration"
    t5 = team.Issue(22, "T5: Fixture universe (CSV) and generator", ())
    assert team.suggest_branch(t5) == "feat/22-fixture-universe-csv-and"


def test_load_tasks_reads_the_git_ref_not_the_working_tree(root: Path) -> None:
    git = ["git", "-C", str(root)]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "-c", "user.email=t@t", "-c", "user.name=t", "add", "docs"], check=True)
    subprocess.run(
        [*git, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "plan"],
        check=True,
    )
    plan = root / "docs" / "plans" / "data-foundation.md"
    plan.write_text(plan.read_text().replace("- [ ] **T5:", "- [x] **T5:"))  # ticked in a PR branch
    from_tree = {t.id: t.done for t in team.load_tasks(root, None)}
    from_ref = {t.id: t.done for t in team.load_tasks(root, "HEAD")}
    assert from_tree["T5"] is True
    assert from_ref["T5"] is False
    with pytest.raises(SystemExit, match="cannot read docs/plans"):
        team.load_tasks(root, "no-such-ref")


# ── claim command ───────────────────────────────────────────────────────────────


def test_claim_creates_issue_comments_and_labels(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGitHub()
    assert team.cmd_claim(gh, root, "T5") == 0
    issue = gh.list_issues("task:T5")[0]
    assert issue.title == "T5: Fixture universe (CSV) and generator"
    assert {"task:T5", "type:feat", "phase:2", "team:atlas"} <= set(issue.labels)
    assert {"task:T5", "type:feat", "phase:2", "team:atlas"} <= gh.labels
    assert gh.comments[issue.number] == ["claim: team:atlas"]
    assert "git switch -c feat/100-fixture-universe-csv-and origin/main" in capsys.readouterr().out


def test_claim_refuses_unready_owner_and_done_tasks(root: Path) -> None:
    gh = FakeGitHub()
    with pytest.raises(SystemExit, match="not ready"):
        team.cmd_claim(gh, root, "T10")
    with pytest.raises(SystemExit, match="owner's keys"):
        team.cmd_claim(gh, root, "T3")
    with pytest.raises(SystemExit, match="already ticked"):
        team.cmd_claim(gh, root, "T4")
    assert gh.issues == {}


def test_owner_task_needs_the_flag_but_any_team_may_claim_it(root: Path) -> None:
    gh = FakeGitHub()
    assert team.cmd_claim(gh, root, "T3", owner_task=True) == 0
    assert "team:atlas" in gh.list_issues("task:T3")[0].labels


def test_claim_loses_to_existing_holder(root: Path) -> None:
    gh = FakeGitHub()
    gh.issues[22] = team.Issue(22, "T5: Fixture universe", ("task:T5",))
    gh.comments[22] = ["claim: team:orion"]
    assert team.cmd_claim(gh, root, "T5") == 1
    assert gh.comments[22] == ["claim: team:orion"]  # we did not post a competing claim
    assert "team:atlas" not in gh.issues[22].labels


def test_claim_concurrent_issue_creation_lower_number_wins(root: Path) -> None:
    gh = FakeGitHub()
    gh.on_create = [50]  # another team's issue lands just before ours (#100)
    assert team.cmd_claim(gh, root, "T5") == 0
    assert gh.closed == [100]
    assert gh.issues[50].state == "OPEN" and "team:atlas" in gh.issues[50].labels


def test_claim_by_number_normalises_hand_made_task_issue(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The 2026-09-24 incident: issues titled 'T5: …' created by hand, no task label."""
    gh = FakeGitHub()
    gh.issues[22] = team.Issue(22, "T5: fixture universe (CSV) and generator", ("type:feat",))
    gh.issues[25] = team.Issue(25, "T5: fixture universe (CSV) and generator", ("type:feat",))
    assert team.cmd_claim(gh, root, "25") == 0  # asked for the higher one
    assert "task:T5" in gh.issues[25].labels
    assert "team:atlas" in gh.issues[25].labels  # 22 was unlabelled, so 25 was canonical then
    capsys.readouterr()
    # Another team claims 22: it gets normalised, becomes canonical, and the tool refuses
    # because the duplicate #25 is held by atlas. Nobody ends up building T5 twice.
    (root / team.TEAM_FILE).write_text("orion\n")
    with pytest.raises(SystemExit, match="held by team 'atlas'"):
        team.cmd_claim(gh, root, "22")
    assert "task:T5" in gh.issues[22].labels
    assert team.resolve_holder(gh.comments.get(22, [])) is None
    # atlas re-running its claim moves to the canonical issue and keeps its duplicate note.
    (root / team.TEAM_FILE).write_text("atlas\n")
    assert team.cmd_claim(gh, root, "T5") == 0
    assert "you also hold duplicate #25" in capsys.readouterr().out
    assert "team:atlas" in gh.issues[22].labels
    # An unheld duplicate is closed by the tool.
    gh.issues[60] = team.Issue(60, "T5: again", ("task:T5",))
    assert team.cmd_claim(gh, root, "T5") == 0
    assert 60 in gh.closed


def test_claim_by_number_routes_to_canonical_task_issue(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGitHub()
    gh.issues[22] = team.Issue(22, "T5: fixture universe", ("task:T5",))
    gh.issues[25] = team.Issue(25, "T5: fixture universe", ("task:T5",))
    assert team.cmd_claim(gh, root, "25") == 0
    assert "canonical issue is #22" in capsys.readouterr().out
    assert "team:atlas" in gh.issues[22].labels
    assert gh.comments[22] == ["claim: team:atlas"] and 25 not in gh.comments


def test_claim_repairs_labels_and_unparks_when_already_holder(root: Path) -> None:
    gh = FakeGitHub()
    gh.issues[22] = team.Issue(22, "T5: fixture universe", ("task:T5", "team:orion"))
    gh.comments[22] = ["claim: team:atlas"]  # label drifted; comments are authoritative
    gh.prs = [_pr(31, "feat/22-fixture-universe", ("parked",))]
    assert team.cmd_claim(gh, root, "T5") == 0
    assert team.team_labels_of(gh.issues[22].labels) == ["atlas"]
    assert "parked" not in gh.prs[0].labels
    assert gh.comments[22] == ["claim: team:atlas"]  # no duplicate claim comment


def test_claim_existing_issue_by_number_and_release_with_park(root: Path) -> None:
    gh = FakeGitHub()
    gh.issues[28] = team.Issue(28, "conftest loader bug", ("size:S", "type:fix"))
    gh.prs = [_pr(40, "fix/28-conftest-loader")]
    assert team.cmd_claim(gh, root, "28") == 0
    assert "team:atlas" in gh.issues[28].labels
    assert team.cmd_release(gh, root, "#28", park=True) == 0
    assert gh.comments[28] == ["claim: team:atlas", "release: team:atlas"]
    assert "team:atlas" not in gh.issues[28].labels
    assert "parked" in gh.prs[0].labels


def test_release_refuses_non_holder_unless_forced_with_reason(root: Path) -> None:
    gh = FakeGitHub()
    gh.issues[28] = team.Issue(28, "bug", ("team:orion",))
    gh.comments[28] = ["claim: team:orion"]
    with pytest.raises(SystemExit, match="held by 'orion'"):
        team.cmd_release(gh, root, "28")
    with pytest.raises(SystemExit, match="--reason"):
        team.cmd_release(gh, root, "28", force=True)
    assert team.cmd_release(gh, root, "28", force=True, reason="window died") == 0
    assert gh.comments[28][1] == "release: team:orion"
    assert "window died" in gh.comments[28][2]
    assert team.resolve_holder(gh.comments[28]) is None
    assert "team:orion" not in gh.issues[28].labels


# ── CI guard ────────────────────────────────────────────────────────────────────


def test_check_claims_fails_the_loser_and_passes_the_survivor(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gh = FakeGitHub()
    gh.issues[22] = team.Issue(22, "T5", ("task:T5", "team:atlas"))
    gh.issues[25] = team.Issue(25, "T5", ("task:T5", "team:orion"))
    gh.comments = {22: ["claim: team:atlas"], 25: ["claim: team:orion"]}
    gh.prs = [_pr(31, "feat/22-fixture-universe"), _pr(34, "feat/25-fixture-universe")]
    assert team.cmd_check_claims(gh, 34) == 1
    assert "lower issue" in capsys.readouterr().out
    assert team.cmd_check_claims(gh, 31) == 0


def test_check_claims_fails_unclaimed_mismatched_and_untagged_issues() -> None:
    gh = FakeGitHub()
    gh.issues[23] = team.Issue(23, "T20: broker", ("task:T20",))
    gh.prs = [_pr(27, "feat/23-fake-broker")]
    assert team.cmd_check_claims(gh, 27) == 1  # unclaimed
    gh.comments[23] = ["claim: team:atlas"]
    assert team.cmd_check_claims(gh, 27) == 1  # claimed but label missing
    gh.add_labels(23, ["team:atlas"])
    assert team.cmd_check_claims(gh, 27) == 0
    gh.add_labels(23, ["team:orion"])
    assert team.cmd_check_claims(gh, 27) == 1  # two team labels
    gh.issues[40] = team.Issue(40, "T8b: delistings", ("team:atlas",))  # titled, no task label
    gh.comments[40] = ["claim: team:atlas"]
    gh.prs.append(_pr(41, "feat/40-delistings"))
    assert team.cmd_check_claims(gh, 41) == 1


def test_check_claims_branch_rules_and_same_issue_duplicates() -> None:
    gh = FakeGitHub()
    gh.issues[28] = team.Issue(28, "bug", ("team:atlas",))
    gh.comments[28] = ["claim: team:atlas"]
    gh.prs = [
        _pr(50, "fix/28-bug"),
        _pr(51, "fix/28-bug-again"),
        _pr(60, "spike/idea"),
        _pr(61, "feat/t5-no-number"),
    ]
    assert team.cmd_check_claims(gh, 50) == 0  # older PR on the issue survives
    assert team.cmd_check_claims(gh, 51) == 1  # one issue, one PR
    assert team.cmd_check_claims(gh, 60) == 0  # spikes are exempt
    assert team.cmd_check_claims(gh, 61) == 1  # no issue number


# ── board ───────────────────────────────────────────────────────────────────────


def test_status_lists_claims_frontier_loose_issues_and_parked(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGitHub()
    gh.issues[22] = team.Issue(22, "T5: Fixture universe", ("task:T5",))
    gh.issues[23] = team.Issue(23, "T20: Broker", ("task:T20", "team:orion"))
    gh.issues[28] = team.Issue(28, "conftest bug", ("size:S",))
    gh.issues[29] = team.Issue(29, "T8b: delistings by hand", ())
    gh.prs = [team.PullRequest(31, "feat/22-fixture-universe", True, ("parked",), "T5 pr")]
    assert team.cmd_status(gh, root) == 0
    out = capsys.readouterr().out
    assert "orion" in out and "#23" in out
    assert "issue #22, PR: #31 parked" in out
    frontier = out.split("READY AND UNCLAIMED")[1].split("UNCLAIMED ISSUES")[0]
    assert "T20" not in frontier
    assert "#28" in out and "titled as T8b: needs its task label" in out
    assert "PARKED PRS" in out
    assert "BLOCKED PLAN TASKS: T8b, T10" in out

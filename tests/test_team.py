"""Tests for scripts/team.py: plan parsing, readiness, claim resolution and the CI guard.

Only the pure functions and the command logic behind a fake GitHub are tested here.
The real ``gh`` wrapper is a thin shell and is exercised by using the tool.
"""

from __future__ import annotations

import importlib.util
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
        self.on_create: list[int] = []  # numbers of issues that "appear" concurrently on create

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
        self.issues[number] = team.Issue(
            i.number, i.title, tuple(dict.fromkeys([*i.labels, *labels])), i.state
        )

    def remove_labels(self, number: int, labels: Sequence[str]) -> None:
        i = self.issues[number]
        self.issues[number] = team.Issue(
            i.number, i.title, tuple(lb for lb in i.labels if lb not in labels), i.state
        )

    def ensure_label(self, name: str, color: str, description: str) -> None:
        self.labels.add(name)

    def list_prs(self) -> list[team.PullRequest]:
        return list(self.prs)

    def get_pr(self, number: int) -> team.PullRequest:
        return next(p for p in self.prs if p.number == number)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "data-foundation.md").write_text(PLAN)
    (tmp_path / team.TEAM_FILE).write_text("atlas\n")
    return tmp_path


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


def test_resolve_holder_earliest_claim_wins_and_release_frees_it() -> None:
    assert team.resolve_holder([]) is None
    assert team.resolve_holder(["claim: team:a", "claim: team:b"]) == "a"
    assert team.resolve_holder(["claim: team:a", "release: team:a", "claim: team:b"]) == "b"
    assert team.resolve_holder(["claim: team:a", "release: team:b"]) == "a"
    assert team.resolve_holder(["Looks good!\nclaim: team:a\nthanks"]) == "a"
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


def test_branch_issue_number_and_suggestion() -> None:
    assert team.issue_number_from_branch("feat/22-fixture-universe") == 22
    assert team.issue_number_from_branch("main") is None
    issue = team.Issue(36, "process: multi-team orchestration (claims)", ("type:docs",))
    assert team.suggest_branch(issue) == "docs/36-process-multi-team-orchestration"
    assert (
        team.suggest_branch(team.Issue(22, "T5: Fixture universe (CSV) and generator", ()))
        == "feat/22-fixture-universe-csv-and"
    )


# ── claim command ───────────────────────────────────────────────────────────────


def test_claim_creates_issue_comments_and_labels(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGitHub()
    assert team.cmd_claim(gh, root, "T5", allow_unready=False) == 0
    issue = gh.list_issues("task:T5")[0]
    assert issue.title == "T5: Fixture universe (CSV) and generator"
    assert {"task:T5", "type:feat", "phase:2", "team:atlas"} <= set(issue.labels)
    assert gh.comments[issue.number] == ["claim: team:atlas"]
    assert "git switch -c feat/100-fixture-universe-csv-and" in capsys.readouterr().out


def test_claim_refuses_unready_owner_and_done_tasks(root: Path) -> None:
    gh = FakeGitHub()
    with pytest.raises(SystemExit, match="not ready"):
        team.cmd_claim(gh, root, "T10", allow_unready=False)
    with pytest.raises(SystemExit, match="owner task"):
        team.cmd_claim(gh, root, "T3", allow_unready=False)
    with pytest.raises(SystemExit, match="already ticked"):
        team.cmd_claim(gh, root, "T4", allow_unready=False)
    assert gh.issues == {}


def test_claim_loses_to_existing_holder(root: Path) -> None:
    gh = FakeGitHub()
    gh.issues[22] = team.Issue(22, "T5: Fixture universe", ("task:T5",))
    gh.comments[22] = ["claim: team:orion"]
    assert team.cmd_claim(gh, root, "T5", allow_unready=False) == 1
    assert gh.comments[22] == ["claim: team:orion"]  # we did not post a competing claim
    assert "team:atlas" not in gh.issues[22].labels


def test_claim_concurrent_issue_creation_lower_number_wins(root: Path) -> None:
    gh = FakeGitHub()
    gh.on_create = [50]  # another team's issue lands just before ours (#100)
    assert team.cmd_claim(gh, root, "T5", allow_unready=False) == 0
    assert gh.closed == [100]
    assert gh.issues[50].state == "OPEN" and "team:atlas" in gh.issues[50].labels


def test_claim_existing_issue_by_number_and_release(root: Path) -> None:
    gh = FakeGitHub()
    gh.issues[28] = team.Issue(28, "conftest loader bug", ("size:S", "type:fix"))
    assert team.cmd_claim(gh, root, "28", allow_unready=False) == 0
    assert "team:atlas" in gh.issues[28].labels
    assert team.cmd_release(gh, root, "#28") == 0
    assert gh.comments[28] == ["claim: team:atlas", "release: team:atlas"]
    assert "team:atlas" not in gh.issues[28].labels


def test_release_refuses_when_not_holder(root: Path) -> None:
    gh = FakeGitHub()
    gh.issues[28] = team.Issue(28, "bug", ())
    gh.comments[28] = ["claim: team:orion"]
    with pytest.raises(SystemExit, match="held by 'orion'"):
        team.cmd_release(gh, root, "28")


# ── CI guard ────────────────────────────────────────────────────────────────────


def _pr(n: int, branch: str) -> team.PullRequest:
    return team.PullRequest(n, branch, True, (), f"pr {n}")


def test_check_claims_fails_on_two_open_prs_for_one_task(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gh = FakeGitHub()
    gh.issues[22] = team.Issue(22, "T5", ("task:T5", "team:atlas"))
    gh.issues[25] = team.Issue(25, "T5", ("task:T5", "team:orion"))
    gh.prs = [_pr(31, "feat/22-fixture-universe"), _pr(34, "feat/25-fixture-universe")]
    assert team.cmd_check_claims(gh, 34) == 1
    out = capsys.readouterr().out
    assert "T5 has 2 open PRs" in out and "#31" in out and "#34" in out


def test_check_claims_fails_on_unclaimed_issue() -> None:
    gh = FakeGitHub()
    gh.issues[23] = team.Issue(23, "T20", ("task:T20",))
    gh.prs = [_pr(27, "feat/23-fake-broker")]
    assert team.cmd_check_claims(gh, 27) == 1


def test_check_claims_passes_single_claimed_pr_and_skips_branches_without_issue() -> None:
    gh = FakeGitHub()
    gh.issues[23] = team.Issue(23, "T20", ("task:T20", "team:atlas"))
    gh.prs = [_pr(27, "feat/23-fake-broker"), _pr(99, "spike/try-something")]
    assert team.cmd_check_claims(gh, 27) == 0
    assert team.cmd_check_claims(gh, 99) == 0


# ── board ───────────────────────────────────────────────────────────────────────


def test_status_lists_claims_frontier_loose_issues_and_parked(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGitHub()
    gh.issues[22] = team.Issue(22, "T5: Fixture universe", ("task:T5",))
    gh.issues[23] = team.Issue(23, "T20: Broker", ("task:T20", "team:orion"))
    gh.issues[28] = team.Issue(28, "conftest bug", ("size:S",))
    gh.prs = [team.PullRequest(31, "feat/22-fixture-universe", True, ("parked",), "T5 pr")]
    assert team.cmd_status(gh, root) == 0
    out = capsys.readouterr().out
    assert "orion" in out and "#23" in out
    assert "T5" in out and "issue #22, PR: #31 parked" in out
    assert "T20" not in out.split("READY AND UNCLAIMED")[1].split("UNCLAIMED ISSUES")[0]
    assert "#28" in out
    assert "PARKED PRS" in out
    assert "BLOCKED PLAN TASKS: T8b, T10" in out

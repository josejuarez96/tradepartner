"""Tests for scripts/team.py: plan parsing, readiness, claim resolution and the CI guard.

Only the pure functions and the command logic behind a fake GitHub are tested here.
The real ``gh`` wrapper is a thin shell and is exercised by using the tool.
"""

from __future__ import annotations

import importlib.util
import os
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

# A second, small plan for the graph tests (#389): a diamond (T3/T4 both gate T7), a file
# (`src/b.py`) named by three open tasks and one done task, and an owner task (T2) with open
# tasks downstream of it.
GRAPH_PLAN = """# Plan: Graph fixture (Phase 9)

## Tasks
- [x] **T1: Config.** Files: `src/a.py` · Depends on: n/a · Review: qa.
- [ ] **T2 (owner): Secrets setup.** Files: `src/b.py` · Depends on: T1 · Review: safety-reviewer.
- [ ] **T3: Branch A.** Files: `src/b.py`, `tests/test_b.py` · Depends on: T2 · Review: qa.
- [ ] **T4: Branch B.** Files: `src/b.py` · Depends on: T2 · Review: qa.
- [ ] **T7: Diamond join.** Files: `src/d.py` · Depends on: T3, T4 · Review: qa.
- [ ] **T5: Chain tail.** Files: `src/c.py` · Depends on: T7 · Review: qa.
- [x] **T6: Done task sharing file.** Files: `src/b.py` · Depends on: T1 · Review: qa.
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


@pytest.fixture
def graph_root(tmp_path: Path) -> Path:
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "graph-fixture.md").write_text(GRAPH_PLAN)
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


def test_task_brief_prints_the_task_line_and_its_dependencies_with_locations() -> None:
    tasks = team.parse_plan(PLAN, "docs/plans/data-foundation.md")
    by_id = {t.id: t for t in tasks}
    lines = team.task_brief(by_id["T10"], tasks).splitlines()
    assert lines[0] == "docs/plans/data-foundation.md:9"
    assert lines[1] == by_id["T10"].line
    assert lines[1].startswith("- [ ] **T10: Suite.**")
    assert "depends on:" in lines
    assert "  docs/plans/data-foundation.md:7" in lines
    assert "  " + by_id["T5"].line in lines
    assert "  docs/plans/data-foundation.md:8" in lines
    assert "  " + by_id["T8b"].line in lines
    # one level only: T4 (T5's dependency) is not printed
    assert not any("**T4:" in line for line in lines)
    # no dependencies (``n/a``): the task line alone, no "depends on:" section
    t1 = team.task_brief(by_id["T1"], tasks).splitlines()
    assert t1 == ["docs/plans/data-foundation.md:4", by_id["T1"].line]
    # a dependency id that is in no plan is named, not skipped
    orphan = team.Task("T9", "x", False, False, ("T99",), "p", 2, "- [ ] **T9: x.**", 1)
    assert "  T99: not a plan task" in team.task_brief(orphan, tasks).splitlines()


def test_show_prints_the_brief_without_touching_github(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert team.cmd_show(root, "T10", ref=None) == 0
    out = capsys.readouterr().out
    assert out.startswith("docs/plans/data-foundation.md:9\n- [ ] **T10: Suite.**")
    assert "**T8b: Delistings.**" in out
    with pytest.raises(SystemExit, match="not a plan task id"):
        team.cmd_show(root, "28", ref=None)


def test_ready_frontier_requires_every_dependency_ticked() -> None:
    tasks = team.parse_plan(PLAN, "p")
    assert [t.id for t in team.ready_tasks(tasks)] == ["T3", "T5", "T20"]


# ── graph: critical path, depth, file contention, owner gates (#389) ───────────────


def test_critical_path_picks_the_longest_open_chain_through_the_diamond() -> None:
    tasks = team.parse_plan(GRAPH_PLAN, "docs/plans/graph-fixture.md")
    # T2 -> T3 -> T7 -> T5 and T2 -> T4 -> T7 -> T5 tie at length 4; T3 < T4 wins the tie.
    assert [t.id for t in team.critical_path(tasks)] == ["T2", "T3", "T7", "T5"]
    # Done tasks never extend or appear in the chain.
    assert all(not t.done for t in team.critical_path(tasks))


def test_depth_levels_group_open_tasks_and_exclude_done_tasks() -> None:
    tasks = team.parse_plan(GRAPH_PLAN, "docs/plans/graph-fixture.md")
    levels = team.depth_levels(tasks)
    assert levels == {1: ["T2"], 2: ["T3", "T4"], 3: ["T7"], 4: ["T5"]}
    assert "T1" not in [i for ids in levels.values() for i in ids]
    assert "T6" not in [i for ids in levels.values() for i in ids]


def test_file_contention_lists_files_named_by_two_or_more_open_tasks() -> None:
    tasks = team.parse_plan(GRAPH_PLAN, "docs/plans/graph-fixture.md")
    contention = team.file_contention(tasks)
    # src/b.py is named by three open tasks (T2, T3, T4); the done T6 sharing it doesn't count.
    assert contention == {"src/b.py": ["T2", "T3", "T4"]}
    # tests/test_b.py and src/d.py are each named by only one open task: no contention.
    assert "tests/test_b.py" not in contention
    assert "src/d.py" not in contention


def test_downstream_of_returns_transitive_open_dependants_of_an_owner_task() -> None:
    tasks = team.parse_plan(GRAPH_PLAN, "docs/plans/graph-fixture.md")
    owner_task = next(t for t in tasks if t.id == "T2")
    assert owner_task.owner is True
    assert team.downstream_of(tasks, "T2") == ["T3", "T4", "T5", "T7"]
    # A task with no dependants has an empty downstream list.
    assert team.downstream_of(tasks, "T5") == []


def test_cmd_graph_prints_the_sections_and_respects_max_depth(
    graph_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert team.cmd_graph(graph_root, ref=None) == 0
    out = capsys.readouterr().out
    assert "CRITICAL PATH" in out
    assert "T2 -> T3 -> T7 -> T5" in out
    assert "DEPTH LEVELS" in out
    assert "3: T7" in out
    assert "FILE CONTENTION" in out
    assert "src/b.py: T2, T3, T4" in out
    assert "OWNER GATES" in out
    assert "T2: T3, T4, T5, T7" in out
    assert "LONGEST CHAIN: 4" in out

    assert team.cmd_graph(graph_root, ref=None, max_depth=4) == 0
    assert team.cmd_graph(graph_root, ref=None, max_depth=3) == 1


# ── graph: code-review follow-ups (#389) ────────────────────────────────────────


def test_critical_path_and_depth_levels_raise_systemexit_on_a_dependency_cycle() -> None:
    plan = """# Plan: Cycle fixture (Phase 9)

## Tasks
- [ ] **T1: Self dependency.** Files: `src/a.py` · Depends on: T1 · Review: qa.
"""
    tasks = team.parse_plan(plan, "docs/plans/cycle-fixture.md")
    with pytest.raises(SystemExit, match="cycle"):
        team.critical_path(tasks)
    with pytest.raises(SystemExit, match="cycle"):
        team.depth_levels(tasks)


def test_cycle_error_excludes_tasks_that_merely_lead_into_the_cycle() -> None:
    # T1 leads into the T2 <-> T3 cycle but is not part of it; the error should name
    # only T2 and T3 (second code review on #389).
    plan = """# Plan: Lead-in cycle fixture (Phase 9)

## Tasks
- [ ] **T1: Leads into a cycle.** Files: `src/a.py` · Depends on: T2 · Review: qa.
- [ ] **T2: Cycle member.** Files: `src/b.py` · Depends on: T3 · Review: qa.
- [ ] **T3: Cycle member.** Files: `src/c.py` · Depends on: T2 · Review: qa.
"""
    tasks = team.parse_plan(plan, "docs/plans/lead-in-cycle-fixture.md")
    with pytest.raises(SystemExit) as exc_info:
        team.critical_path(tasks)
    message = str(exc_info.value)
    assert "T1" not in message
    assert "T2" in message
    assert "T3" in message


def test_downstream_of_seeds_visited_with_the_start_task() -> None:
    # T1 (open, owner) depends on T2 (done); T2 depends back on T1. Without seeding
    # ``visited`` with the start task, this cycle through a done task made T1 its own
    # dependant (second code review on #389).
    plan = """# Plan: Self-cycle-through-done fixture (Phase 9)

## Tasks
- [ ] **T1 (owner): Root.** Files: `src/a.py` · Depends on: T2 · Review: qa.
- [x] **T2: Done, cycles back to T1.** Files: `src/b.py` · Depends on: T1 · Review: qa.
"""
    tasks = team.parse_plan(plan, "docs/plans/self-cycle-through-done-fixture.md")
    assert team.downstream_of(tasks, "T1") == []


def test_task_files_counts_known_extensionless_root_files() -> None:
    plan = """# Plan: Dotfiles fixture (Phase 9)

## Tasks
- [ ] **T1: Dotfiles.** Files: `.python-version`, `.unstamped_filings` · Depends on: n/a · r: qa
"""
    tasks = team.parse_plan(plan, "docs/plans/root-dotfiles-fixture.md")
    assert team.task_files(tasks[0]) == [".python-version"]


def test_file_contention_matches_a_brace_pair_with_its_plain_sibling() -> None:
    plan = """# Plan: Brace fixture (Phase 9)

## Tasks
- [ ] **T1: Adapters.** Files: `src/tradepartner/adapters/{broker,fake_broker}.py` \
· Depends on: n/a · r: qa
- [ ] **T2: Broker only.** Files: `src/tradepartner/adapters/broker.py` · Depends on: n/a · r: qa
"""
    tasks = team.parse_plan(plan, "docs/plans/brace-fixture.md")
    contention = team.file_contention(tasks)
    assert contention == {"src/tradepartner/adapters/broker.py": ["T1", "T2"]}
    # fake_broker.py is only named by T1 (via the brace), so it isn't contended.
    assert "src/tradepartner/adapters/fake_broker.py" not in contention


def test_file_contention_matches_a_glob_directory_with_a_file_under_it() -> None:
    plan = """# Plan: Glob fixture (Phase 9)

## Tasks
- [ ] **T1: Fixtures.** Files: `tests/fixtures/alpaca/paper/*` · Depends on: n/a · r: qa
- [ ] **T2: One fixture.** Files: `tests/fixtures/alpaca/paper/orders.json` \
· Depends on: n/a · r: qa
"""
    tasks = team.parse_plan(plan, "docs/plans/glob-fixture.md")
    contention = team.file_contention(tasks)
    assert contention == {"tests/fixtures/alpaca/paper/orders.json": ["T1", "T2"]}


def test_file_contention_matches_a_stem_with_its_full_path() -> None:
    plan = """# Plan: Stem fixture (Phase 9)

## Tasks
- [ ] **T1: Full path.** Files: `src/tradepartner/execution/wrapper.py` \
· Depends on: n/a · r: qa
- [ ] **T2: Bare stem.** Files: `execution/wrapper.py` · Depends on: n/a · r: qa
"""
    tasks = team.parse_plan(plan, "docs/plans/stem-fixture.md")
    contention = team.file_contention(tasks)
    assert contention == {"src/tradepartner/execution/wrapper.py": ["T1", "T2"]}


def test_task_files_ignores_a_url() -> None:
    plan = """# Plan: URL fixture (Phase 9)

## Tasks
- [ ] **T1: Docs link.** Files: `https://example.com/foo.py` · Depends on: n/a · r: qa
- [ ] **T2: Same link.** Files: `https://example.com/foo.py` · Depends on: n/a · r: qa
"""
    tasks = team.parse_plan(plan, "docs/plans/url-fixture.md")
    assert team.task_files(tasks[0]) == []
    assert team.file_contention(tasks) == {}


def test_file_contention_dedupes_a_file_named_twice_by_one_task() -> None:
    plan = """# Plan: Dedupe fixture (Phase 9)

## Tasks
- [ ] **T1: Repeats a file.** Files: `src/a.py`, `src/a.py` · Depends on: n/a · Review: qa.
- [ ] **T2: Doesn't touch it.** Files: `src/zzz.py` · Depends on: n/a · Review: qa.
"""
    tasks = team.parse_plan(plan, "docs/plans/dedupe-fixture.md")
    assert team.file_contention(tasks) == {}


def test_file_contention_counts_root_level_files_by_extension() -> None:
    plan = """# Plan: Root file fixture (Phase 9)

## Tasks
- [ ] **T1: Touches the changelog.** Files: `CHANGELOG.md` · Depends on: n/a · Review: qa.
- [ ] **T2: Also touches the changelog.** Files: `CHANGELOG.md` · Depends on: n/a · Review: qa.
- [ ] **T3: Names a bare dotfile.** Files: `.unstamped_filings` · Depends on: n/a · Review: qa.
"""
    tasks = team.parse_plan(plan, "docs/plans/root-fixture.md")
    assert team.file_contention(tasks) == {"CHANGELOG.md": ["T1", "T2"]}
    t3 = next(t for t in tasks if t.id == "T3")
    assert team.task_files(t3) == []


def test_downstream_of_continues_through_a_done_task() -> None:
    plan = """# Plan: Downstream-through-done fixture (Phase 9)

## Tasks
- [ ] **T1 (owner): Root.** Files: `src/a.py` · Depends on: n/a · Review: qa.
- [x] **T2: Done midpoint.** Files: `src/b.py` · Depends on: T1 · Review: qa.
- [ ] **T3: Behind the done task.** Files: `src/c.py` · Depends on: T2 · Review: qa.
"""
    tasks = team.parse_plan(plan, "docs/plans/downstream-done-fixture.md")
    assert team.downstream_of(tasks, "T1") == ["T3"]


def test_owner_gates_reports_an_owner_task_off_the_critical_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = """# Plan: Owner gates fixture (Phase 9)

## Tasks
- [ ] **T1 (owner): On the critical path.** Files: `src/a.py` · Depends on: n/a · Review: qa.
- [ ] **T2: Chain continues.** Files: `src/b.py` · Depends on: T1 · Review: qa.
- [ ] **T3: Chain tail.** Files: `src/c.py` · Depends on: T2 · Review: qa.
- [ ] **T4 (owner): Off the critical path.** Files: `src/d.py` · Depends on: n/a · Review: qa.
- [ ] **T5: Downstream of T4 only.** Files: `src/e.py` · Depends on: T4 · Review: qa.
- [ ] **T6: Also downstream of T4.** Files: `src/f.py` · Depends on: T4 · Review: qa.
"""
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "gates-fixture.md").write_text(plan)
    assert team.cmd_graph(tmp_path, ref=None) == 0
    out = capsys.readouterr().out
    # T1 is on the critical path (T1 -> T2 -> T3, length 3) and marked with "*".
    assert "* T1: T2, T3" in out
    # T4 is off the critical path (T4 -> T5/T6, length 2) but still reported, unmarked.
    assert "T4: T5, T6" in out
    assert "* T4" not in out
    # T1 is listed before T4 (more downstream tasks first; both have 2 here, so by id).
    assert out.index("T1: T2, T3") < out.index("T4: T5, T6")


def test_task_sort_key_orders_task_ids_numerically_not_lexically() -> None:
    ids = ["T10", "T2", "T1", "T9b", "T9a"]
    assert sorted(ids, key=team.task_sort_key) == ["T1", "T2", "T9a", "T9b", "T10"]


def test_depth_levels_sorts_same_depth_ids_numerically() -> None:
    plan = """# Plan: Numeric sort fixture (Phase 9)

## Tasks
- [ ] **T10: Tenth.** Files: `src/j.py` · Depends on: n/a · Review: qa.
- [ ] **T2: Second.** Files: `src/b.py` · Depends on: n/a · Review: qa.
"""
    tasks = team.parse_plan(plan, "docs/plans/numeric-fixture.md")
    assert team.depth_levels(tasks) == {1: ["T2", "T10"]}


def test_main_graph_ref_mapping_env_default_and_cli_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Confirms the *mapping* `graph`'s --ref uses when omitted (#389 third code review):
    # an empty TRADEPARTNER_PLAN_REF means the working tree, same as plan_ref_from_env's
    # own mapping; an explicit --ref overrides it outright.
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    plan_path = tmp_path / "docs" / "plans" / "mini.md"
    plan_path.write_text(
        "# Plan: Mini (Phase 9)\n\n## Tasks\n"
        "- [ ] **T1: Solo task.** Files: `src/a.py` · Depends on: n/a · Review: qa.\n"
    )
    _init_repo(tmp_path)  # commits the plan with T1 open
    plan_path.write_text(plan_path.read_text().replace("- [ ]", "- [x]"))  # ticked, uncommitted
    monkeypatch.chdir(tmp_path)

    # TRADEPARTNER_PLAN_REF="" maps to the working tree (plan_ref_from_env's own mapping),
    # where T1 is now ticked: no open tasks at all.
    monkeypatch.setenv(team.PLAN_REF_ENV, "")
    assert team.main(["graph"], gh=FakeGitHub()) == 0
    assert "LONGEST CHAIN: 0" in capsys.readouterr().out

    # --ref HEAD overrides the env var outright and reads the committed plan, where T1
    # is still open.
    assert team.main(["graph", "--ref", "HEAD"], gh=FakeGitHub()) == 0
    assert "LONGEST CHAIN: 1" in capsys.readouterr().out


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


def test_merged_task_ids_reads_squash_merged_code_subjects() -> None:
    subjects = [
        "feat(data): statement facts health metrics and page block (T77c) (#1067)",
        "test(lookahead): look-ahead suites at every cadence (T98b) (#1100)",
        "docs(plans): amend the T85e line (T85e) (#1074)",  # a plan edit is not the task
        "feat(backtest): sweep parser (#1097)",  # no task id
        "fix(config): something (T12)",  # not a squash merge (no PR number)
    ]
    assert team.merged_task_ids(subjects) == frozenset({"T77c", "T98b"})


def test_a_task_merged_on_the_ref_counts_as_done_before_its_box_is_ticked(root: Path) -> None:
    """#1130: a merged PR waiting for a fold no longer blocks its dependants."""
    git = ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "-C", str(root), "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "add", "docs"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "plan"], check=True)
    subprocess.run(
        [*git, "commit", "-q", "--allow-empty", "-m", "feat(data): fixture universe (T5) (#22)"],
        check=True,
    )
    tasks = team.load_tasks(root, "HEAD")
    assert {t.id: t.done for t in tasks}["T5"] is True
    assert "T8b" in [t.id for t in team.ready_tasks(tasks)]
    assert {t.id: t.done for t in team.load_tasks(root, None)}["T5"] is False  # working tree


# ── start and register ──────────────────────────────────────────────────────────


def _init_repo(root: Path) -> None:
    git = ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "add", "docs"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "plan"], check=True)


def test_start_creates_a_worktree_outside_the_repo_and_registers_it(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _init_repo(root)
    gh = FakeGitHub()
    assert team.cmd_start(gh, root, "guilo", ref="HEAD") == 0
    path = root.parent / f"{root.name}-teams" / "guilo"
    assert path.is_dir() and not path.is_relative_to(root)
    assert (path / team.TEAM_FILE).read_text().strip() == "guilo"
    assert (path / "docs" / "plans" / "data-foundation.md").exists()
    assert "team:guilo" in gh.labels
    assert f"cd {path}" in capsys.readouterr().out
    assert (root / team.TEAM_FILE).read_text().strip() == "atlas"  # main checkout untouched
    with pytest.raises(SystemExit, match="already exists"):
        team.cmd_start(gh, root, "guilo", ref="HEAD")


def test_start_refuses_a_name_that_holds_open_issues(root: Path) -> None:
    _init_repo(root)
    gh = FakeGitHub()
    gh.issues[30] = team.Issue(30, "chore", ("team:orion",))
    with pytest.raises(SystemExit, match="holds open issues \\[30\\]"):
        team.cmd_start(gh, root, "orion", ref="HEAD")
    assert team.cmd_start(gh, root, "orion", ref="HEAD", reuse=True) == 0


def test_register_never_overwrites_another_teams_directory(root: Path) -> None:
    gh = FakeGitHub()
    with pytest.raises(SystemExit, match="already belongs to team 'atlas'"):
        team.cmd_register(gh, root, "orion")
    assert (root / team.TEAM_FILE).read_text().strip() == "atlas"
    assert team.cmd_register(gh, root, "atlas") == 0  # same name is fine
    assert team.cmd_register(gh, root, "orion", force=True) == 0
    assert (root / team.TEAM_FILE).read_text().strip() == "orion"


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
    out = capsys.readouterr().out
    assert "git switch -c feat/100-fixture-universe-csv-and origin/main" in out
    # the claim prints the task's own line and its dependency lines (#352)
    assert "your task, from the merged plan" in out
    assert "- [ ] **T5: Fixture universe (CSV) and generator.**" in out
    assert "- [x] **T4: Store schema.**" in out


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


# ── prune ───────────────────────────────────────────────────────────────────────


def _td(name: str, idle_h: float, *, dirty: bool = False, registered: bool = True) -> team.TeamDir:
    return team.TeamDir(name, Path("/t") / name, 1_000_000.0 - idle_h * 3600, dirty, registered)


def test_prune_candidates_keeps_active_recent_dirty_and_unregistered() -> None:
    dirs = [
        _td("old", 30),
        _td("busy", 30),
        _td("fresh", 2),
        _td("messy", 30, dirty=True),
        _td("stray", 30, registered=False),
        _td("older", 100),
    ]
    remove, skipped = team.prune_candidates(dirs, {"busy"}, now=1_000_000.0, hours=6)
    assert [d.name for d in remove] == ["old", "older"]
    reasons = {d.name: why for d, why in skipped}
    assert "claim" in reasons["busy"]
    assert "2.0h" in reasons["fresh"]
    assert "uncommitted" in reasons["messy"]
    assert ".team" in reasons["stray"]


def test_prune_dry_run_by_default_and_removes_with_yes(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (root / ".gitignore").write_text(".team\n")  # as in the real repo
    _init_repo(root)
    git = ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "add", ".gitignore"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "ignore .team"], check=True)
    gh = FakeGitHub()
    team.cmd_start(gh, root, "guilo", ref="HEAD")
    team.cmd_start(gh, root, "busy", ref="HEAD")
    gh.issues[7] = team.Issue(7, "x", ("team:busy",))
    base = root.parent / f"{root.name}-teams"
    old = 1_000.0
    for p in [base / "guilo", *(base / "guilo").iterdir()]:
        os.utime(p, (old, old))

    assert team.cmd_prune(gh, root, hours=6) == 0
    out = capsys.readouterr().out
    assert "would remove guilo" in out and "keep    busy" in out and "dry run" in out
    assert (base / "guilo").is_dir()

    assert team.cmd_prune(gh, root, hours=6, yes=True) == 0
    assert not (base / "guilo").exists()
    assert (base / "busy").is_dir()
    assert "removed 1" in capsys.readouterr().out

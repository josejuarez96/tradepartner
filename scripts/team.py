#!/usr/bin/env python3
"""Team orchestration CLI: claims, the lanes board and the CI duplicate-claim guard.

Rules live in ``docs/ways-of-working/teams.md``. GitHub issues, labels and comments
are the source of truth for who owns what; this script is a thin, deterministic front
end over the ``gh`` CLI. Pure logic (plan parsing, readiness, claim resolution) does no
I/O so it can be unit-tested; every GitHub call goes through the ``GitHub`` protocol.

Usage (from a team's clone)::

    uv run python scripts/team.py register <name>
    uv run python scripts/team.py whoami
    uv run python scripts/team.py status
    uv run python scripts/team.py claim T5          # plan task
    uv run python scripts/team.py claim 28          # existing issue
    uv run python scripts/team.py release T5
    uv run python scripts/team.py check-claims --pr 31
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

TEAM_FILE = ".team"
TEAM_ENV = "TRADEPARTNER_TEAM"
TEAM_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,19}$")
TASK_ID_RE = re.compile(r"^T\d+[a-z]?$")
TASK_REF_RE = re.compile(r"T\d+[a-z]?")
TASK_LINE_RE = re.compile(
    r"^- \[(?P<done>[ x])\] \*\*(?P<id>T\d+[a-z]?)(?P<owner> \(owner\))?: (?P<title>.*?)\*\*"
)
DEPENDS_RE = re.compile(r"Depends on:\s*([^·]+)")
PHASE_RE = re.compile(r"\(Phase (\d+)\)")
BRANCH_ISSUE_RE = re.compile(r"^[a-z]+/(\d+)-")
CLAIM_LINE_RE = re.compile(r"^(claim|release):\s*team:([a-z][a-z0-9-]{0,19})\s*$")
TEAM_LABEL_PREFIX = "team:"
TASK_LABEL_PREFIX = "task:"
PARKED_LABEL = "parked"
TEAM_LABEL_COLOR = "0E8A16"
TASK_LABEL_COLOR = "5319E7"
TYPE_TO_PREFIX = {
    "type:fix": "fix",
    "type:docs": "docs",
    "type:chore": "chore",
    "type:research": "research",
}


# ── data ────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Task:
    """One checkbox line from a plan file."""

    id: str
    title: str
    done: bool
    owner: bool
    depends_on: tuple[str, ...]
    plan: str
    phase: int | None


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    labels: tuple[str, ...]
    state: str = "OPEN"


@dataclass(frozen=True)
class PullRequest:
    number: int
    branch: str
    draft: bool
    labels: tuple[str, ...]
    title: str


# ── pure logic ──────────────────────────────────────────────────────────────────


def parse_plan(text: str, plan: str) -> list[Task]:
    """Extract tasks from a plan file written to the docs/templates/plan.md shape."""
    phase_match = PHASE_RE.search(text.splitlines()[0] if text else "")
    phase = int(phase_match.group(1)) if phase_match else None
    tasks: list[Task] = []
    for line in text.splitlines():
        m = TASK_LINE_RE.match(line)
        if not m:
            continue
        deps_match = DEPENDS_RE.search(line)
        deps = tuple(TASK_REF_RE.findall(deps_match.group(1))) if deps_match else ()
        tasks.append(
            Task(
                id=m.group("id"),
                title=m.group("title").rstrip("."),
                done=m.group("done") == "x",
                owner=m.group("owner") is not None,
                depends_on=deps,
                plan=plan,
                phase=phase,
            )
        )
    return tasks


def is_ready(task: Task, by_id: dict[str, Task]) -> bool:
    """A task is ready when it is not done and every dependency is ticked in the plan."""
    if task.done:
        return False
    return all(dep in by_id and by_id[dep].done for dep in task.depends_on)


def ready_tasks(tasks: Sequence[Task]) -> list[Task]:
    by_id = {t.id: t for t in tasks}
    return [t for t in tasks if is_ready(t, by_id)]


def resolve_holder(comment_bodies: Iterable[str]) -> str | None:
    """Replay claim/release comments in order. The earliest unreleased claim wins."""
    holder: str | None = None
    for body in comment_bodies:
        for line in body.splitlines():
            m = CLAIM_LINE_RE.match(line.strip())
            if not m:
                continue
            verb, team = m.group(1), m.group(2)
            if verb == "claim" and holder is None:
                holder = team
            elif verb == "release" and holder == team:
                holder = None
    return holder


def canonical_issue(issues: Sequence[Issue]) -> Issue | None:
    """When several open issues carry the same task label, the lowest number wins."""
    open_issues = [i for i in issues if i.state.upper() == "OPEN"]
    return min(open_issues, key=lambda i: i.number) if open_issues else None


def issue_number_from_branch(branch: str) -> int | None:
    m = BRANCH_ISSUE_RE.match(branch)
    return int(m.group(1)) if m else None


def team_of(labels: Iterable[str]) -> str | None:
    teams = [
        lb.removeprefix(TEAM_LABEL_PREFIX) for lb in labels if lb.startswith(TEAM_LABEL_PREFIX)
    ]
    return teams[0] if teams else None


def tasks_of(labels: Iterable[str]) -> list[str]:
    return [lb.removeprefix(TASK_LABEL_PREFIX) for lb in labels if lb.startswith(TASK_LABEL_PREFIX)]


def duplicate_prs(task_issues: Sequence[Issue], prs: Sequence[PullRequest]) -> list[PullRequest]:
    """Open PRs whose branch points at any issue carrying the task label."""
    numbers = {i.number for i in task_issues}
    return sorted(
        (p for p in prs if issue_number_from_branch(p.branch) in numbers), key=lambda p: p.number
    )


def slugify(title: str, max_words: int = 4) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", title.lower()).split()
    return "-".join(words[:max_words]) or "task"


def suggest_branch(issue: Issue) -> str:
    prefix = next((p for lb, p in TYPE_TO_PREFIX.items() if lb in issue.labels), "feat")
    title = re.sub(r"^T\d+[a-z]?:\s*", "", issue.title)
    return f"{prefix}/{issue.number}-{slugify(title)}"


# ── GitHub access ───────────────────────────────────────────────────────────────


class GitHub(Protocol):
    def list_issues(self, label: str | None = None) -> list[Issue]: ...
    def get_issue(self, number: int) -> Issue: ...
    def issue_comments(self, number: int) -> list[str]: ...
    def create_issue(self, title: str, body: str, labels: Sequence[str]) -> int: ...
    def close_issue(self, number: int, comment: str) -> None: ...
    def comment(self, number: int, body: str) -> None: ...
    def add_labels(self, number: int, labels: Sequence[str]) -> None: ...
    def remove_labels(self, number: int, labels: Sequence[str]) -> None: ...
    def ensure_label(self, name: str, color: str, description: str) -> None: ...
    def list_prs(self) -> list[PullRequest]: ...
    def get_pr(self, number: int) -> PullRequest: ...


class GhCli:
    """Real GitHub access through the ``gh`` CLI (needs ``gh auth`` or ``GH_TOKEN``)."""

    def _run(self, *args: str) -> str:
        try:
            return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout
        except FileNotFoundError:
            raise SystemExit("gh CLI not found; install it and run `gh auth login`") from None
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f"gh {' '.join(args[:2])} failed: {exc.stderr.strip()}") from None

    @staticmethod
    def _issue(raw: dict[str, object]) -> Issue:
        labels = tuple(str(lb["name"]) for lb in raw.get("labels", []))  # type: ignore[index,union-attr]
        return Issue(int(raw["number"]), str(raw["title"]), labels, str(raw.get("state", "OPEN")))  # type: ignore[arg-type]

    @staticmethod
    def _pr(raw: dict[str, object]) -> PullRequest:
        labels = tuple(str(lb["name"]) for lb in raw.get("labels", []))  # type: ignore[index,union-attr]
        return PullRequest(
            int(raw["number"]),  # type: ignore[arg-type]
            str(raw["headRefName"]),
            bool(raw["isDraft"]),
            labels,
            str(raw["title"]),
        )

    def list_issues(self, label: str | None = None) -> list[Issue]:
        args = [
            "issue",
            "list",
            "--state",
            "open",
            "--limit",
            "200",
            "--json",
            "number,title,labels,state",
        ]
        if label:
            args += ["--label", label]
        return [self._issue(r) for r in json.loads(self._run(*args))]

    def get_issue(self, number: int) -> Issue:
        return self._issue(
            json.loads(
                self._run("issue", "view", str(number), "--json", "number,title,labels,state")
            )
        )

    def issue_comments(self, number: int) -> list[str]:
        raw = json.loads(self._run("issue", "view", str(number), "--json", "comments"))
        return [str(c["body"]) for c in raw.get("comments", [])]

    def create_issue(self, title: str, body: str, labels: Sequence[str]) -> int:
        args = ["issue", "create", "--title", title, "--body", body]
        for lb in labels:
            args += ["--label", lb]
        url = self._run(*args).strip()
        return int(url.rsplit("/", 1)[-1])

    def close_issue(self, number: int, comment: str) -> None:
        self._run("issue", "close", str(number), "--reason", "not planned", "--comment", comment)

    def comment(self, number: int, body: str) -> None:
        self._run("issue", "comment", str(number), "--body", body)

    def add_labels(self, number: int, labels: Sequence[str]) -> None:
        args = ["issue", "edit", str(number)]
        for lb in labels:
            args += ["--add-label", lb]
        self._run(*args)

    def remove_labels(self, number: int, labels: Sequence[str]) -> None:
        args = ["issue", "edit", str(number)]
        for lb in labels:
            args += ["--remove-label", lb]
        self._run(*args)

    def ensure_label(self, name: str, color: str, description: str) -> None:
        self._run(
            "label", "create", name, "--color", color, "--description", description, "--force"
        )

    def list_prs(self) -> list[PullRequest]:
        raw = self._run(
            "pr",
            "list",
            "--state",
            "open",
            "--limit",
            "200",
            "--json",
            "number,headRefName,isDraft,labels,title",
        )
        return [self._pr(r) for r in json.loads(raw)]

    def get_pr(self, number: int) -> PullRequest:
        raw = self._run(
            "pr", "view", str(number), "--json", "number,headRefName,isDraft,labels,title"
        )
        return self._pr(json.loads(raw))


# ── local state ─────────────────────────────────────────────────────────────────


def repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], check=True, capture_output=True, text=True
    )
    return Path(out.stdout.strip())


def current_team(root: Path) -> str | None:
    env = os.environ.get(TEAM_ENV)
    if env:
        return env.strip()
    path = root / TEAM_FILE
    return path.read_text().strip() if path.exists() else None


def require_team(root: Path) -> str:
    team = current_team(root)
    if not team or not TEAM_NAME_RE.match(team):
        raise SystemExit(
            "no team registered in this clone; run `uv run python scripts/team.py register <name>`"
        )
    return team


def load_tasks(root: Path) -> list[Task]:
    tasks: list[Task] = []
    for plan in sorted((root / "docs" / "plans").glob("*.md")):
        tasks.extend(parse_plan(plan.read_text(), plan.relative_to(root).as_posix()))
    return tasks


# ── commands ────────────────────────────────────────────────────────────────────


def cmd_register(gh: GitHub, root: Path, name: str) -> int:
    if not TEAM_NAME_RE.match(name):
        raise SystemExit("team name must match ^[a-z][a-z0-9-]{0,19}$ (e.g. atlas, team-b)")
    (root / TEAM_FILE).write_text(name + "\n")
    gh.ensure_label(f"{TEAM_LABEL_PREFIX}{name}", TEAM_LABEL_COLOR, f"Claimed by team {name}")
    print(
        f"registered team '{name}' in {root / TEAM_FILE}; label {TEAM_LABEL_PREFIX}{name} ensured"
    )
    return 0


def _find_task(tasks: Sequence[Task], task_id: str) -> Task:
    for t in tasks:
        if t.id == task_id:
            return t
    raise SystemExit(f"{task_id} is not in any plan under docs/plans/")


def _task_issue(gh: GitHub, task: Task, create: bool) -> Issue | None:
    """Return the canonical open issue for a plan task, creating one if asked and absent."""
    label = f"{TASK_LABEL_PREFIX}{task.id}"
    existing = canonical_issue(gh.list_issues(label))
    if existing or not create:
        return existing
    gh.ensure_label(label, TASK_LABEL_COLOR, f"Plan task {task.id}")
    labels = [label, "type:feat"] + ([f"phase:{task.phase}"] if task.phase else [])
    body = (
        f"Implements plan task **{task.id}** in `{task.plan}`.\n\n"
        f"Depends on: {', '.join(task.depends_on) or 'n/a'}.\n\n"
        "Created by `scripts/team.py claim`; see docs/ways-of-working/teams.md. "
        "The plan line is the spec-lite: files, tests, dependencies and required review."
    )
    created = gh.create_issue(f"{task.id}: {task.title}", body, labels)
    winner = canonical_issue(gh.list_issues(label))
    if winner is None:
        raise SystemExit(f"created #{created} but cannot see it yet; retry the claim")
    if winner.number != created:
        gh.close_issue(
            created, f"Duplicate of #{winner.number} (created concurrently; lower number wins)."
        )
        print(f"another team created #{winner.number} first; closed our #{created} as a duplicate")
    return winner


def cmd_claim(gh: GitHub, root: Path, target: str, allow_unready: bool) -> int:
    team = require_team(root)
    if TASK_ID_RE.match(target):
        tasks = load_tasks(root)
        task = _find_task(tasks, target)
        if task.done:
            raise SystemExit(f"{task.id} is already ticked in {task.plan}")
        if task.owner:
            raise SystemExit(f"{task.id} is an owner task; agents do not claim it")
        if not is_ready(task, {t.id: t for t in tasks}) and not allow_unready:
            missing = [d for d in task.depends_on if not any(t.id == d and t.done for t in tasks)]
            raise SystemExit(
                f"{task.id} is not ready: unmerged dependencies {missing}. "
                "Use --allow-unready only with stubs agreed in the issue."
            )
        issue = _task_issue(gh, task, create=True)
        assert issue is not None
    else:
        issue = gh.get_issue(int(target.lstrip("#")))
        if issue.state.upper() != "OPEN":
            raise SystemExit(f"#{issue.number} is {issue.state.lower()}")

    holder = resolve_holder(gh.issue_comments(issue.number))
    if holder == team:
        print(f"#{issue.number} is already claimed by {team}")
        return 0
    if holder is not None:
        print(f"#{issue.number} is claimed by team '{holder}'; pick something else (see `status`)")
        return 1
    gh.comment(issue.number, f"claim: team:{team}")
    holder = resolve_holder(gh.issue_comments(issue.number))
    if holder != team:
        print(f"lost the race for #{issue.number} to team '{holder}'")
        return 1
    gh.ensure_label(f"{TEAM_LABEL_PREFIX}{team}", TEAM_LABEL_COLOR, f"Claimed by team {team}")
    gh.add_labels(issue.number, [f"{TEAM_LABEL_PREFIX}{team}"])
    print(f"claimed #{issue.number} '{issue.title}' for team {team}")
    print(f"branch: git switch main && git pull && git switch -c {suggest_branch(issue)}")
    return 0


def cmd_release(gh: GitHub, root: Path, target: str) -> int:
    team = require_team(root)
    if TASK_ID_RE.match(target):
        issue = _task_issue(gh, _find_task(load_tasks(root), target), create=False)
        if issue is None:
            raise SystemExit(f"no open issue carries {TASK_LABEL_PREFIX}{target}")
    else:
        issue = gh.get_issue(int(target.lstrip("#")))
    holder = resolve_holder(gh.issue_comments(issue.number))
    if holder != team:
        raise SystemExit(f"#{issue.number} is held by '{holder}', not by {team}")
    gh.comment(issue.number, f"release: team:{team}")
    gh.remove_labels(issue.number, [f"{TEAM_LABEL_PREFIX}{team}"])
    print(f"released #{issue.number}; leave a comment there on what was finished and what remains")
    return 0


def cmd_status(gh: GitHub, root: Path) -> int:
    tasks = load_tasks(root)
    issues = gh.list_issues()
    prs = gh.list_prs()
    pr_by_issue: dict[int, list[PullRequest]] = {}
    for p in prs:
        n = issue_number_from_branch(p.branch)
        if n is not None:
            pr_by_issue.setdefault(n, []).append(p)

    def pr_cell(n: int) -> str:
        cells = []
        for p in pr_by_issue.get(n, []):
            state = "parked" if PARKED_LABEL in p.labels else ("draft" if p.draft else "ready")
            cells.append(f"#{p.number} {state}")
        return ", ".join(cells) or "-"

    me = current_team(root)
    print(f"team: {me or '(none registered)'}\n")
    print("CLAIMED (open issues with a team label)")
    claimed = sorted(
        (i for i in issues if team_of(i.labels)), key=lambda i: (team_of(i.labels) or "", i.number)
    )
    for i in claimed:
        task = ",".join(tasks_of(i.labels)) or "-"
        row = f"  {team_of(i.labels):<12} #{i.number:<4} {task:<5} {i.title[:60]:<60}"
        print(f"{row} PR: {pr_cell(i.number)}")
    if not claimed:
        print("  none")

    claimed_tasks = {t for i in issues for t in tasks_of(i.labels) if team_of(i.labels)}
    print("\nREADY AND UNCLAIMED (plan tasks whose dependencies are merged)")
    frontier = [t for t in ready_tasks(tasks) if t.id not in claimed_tasks]
    for t in frontier:
        who = "owner" if t.owner else "agent"
        existing = canonical_issue([i for i in issues if f"{TASK_LABEL_PREFIX}{t.id}" in i.labels])
        note = (
            f"issue #{existing.number}, PR: {pr_cell(existing.number)}"
            if existing
            else "no issue yet"
        )
        print(f"  {t.id:<5} {who:<6} {t.title[:60]:<60} {note}")
    if not frontier:
        print("  none")

    print("\nUNCLAIMED ISSUES (no team label, not a plan task)")
    loose = [i for i in issues if not team_of(i.labels) and not tasks_of(i.labels)]
    for i in loose:
        flags = " ".join(lb for lb in i.labels if lb.startswith(("size:", "type:", "blocked")))
        print(f"  #{i.number:<4} {i.title[:70]:<70} {flags}")
    if not loose:
        print("  none")

    parked = [p for p in prs if PARKED_LABEL in p.labels]
    if parked:
        print("\nPARKED PRS (green, waiting to be re-claimed)")
        for p in parked:
            print(f"  #{p.number} {p.branch} {p.title[:60]}")
    blocked_ids = [t.id for t in tasks if not t.done and not is_ready(t, {x.id: x for x in tasks})]
    print(f"\nBLOCKED PLAN TASKS: {', '.join(blocked_ids) or 'none'}")
    return 0


def cmd_check_claims(gh: GitHub, pr_number: int) -> int:
    """CI guard: fail when a plan task has more than one open PR, or the PR's issue is unclaimed."""
    pr = gh.get_pr(pr_number)
    n = issue_number_from_branch(pr.branch)
    if n is None:
        print(f"warning: branch '{pr.branch}' has no issue number; skipping claim check")
        return 0
    issue = gh.get_issue(n)
    problems: list[str] = []
    if team_of(issue.labels) is None:
        problems.append(
            f"issue #{n} has no team label; run `scripts/team.py claim {n}` (or its task id) first"
        )
    prs = gh.list_prs()
    for task in tasks_of(issue.labels):
        task_issues = gh.list_issues(f"{TASK_LABEL_PREFIX}{task}")
        dups = duplicate_prs(task_issues, prs)
        if len(dups) > 1:
            listing = ", ".join(f"#{p.number} ({p.branch})" for p in dups)
            problems.append(
                f"{task} has {len(dups)} open PRs: {listing}. "
                "Lowest issue number wins; close the rest."
            )
    if problems:
        print("claim check FAILED")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(
        f"claim check passed: PR #{pr_number} -> issue #{n}, "
        f"team {team_of(issue.labels)}, tasks {tasks_of(issue.labels) or '-'}"
    )
    return 0


# ── entry point ─────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="team.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("register", help="name this clone's team and create its label")
    p.add_argument("name")
    sub.add_parser("whoami", help="print this clone's team")
    sub.add_parser("status", help="lanes board: claims, ready frontier, loose issues, parked PRs")
    p = sub.add_parser("claim", help="claim a plan task (T5) or an issue (28)")
    p.add_argument("target")
    p.add_argument(
        "--allow-unready", action="store_true", help="claim although dependencies are unmerged"
    )
    p = sub.add_parser("release", help="give a claim back")
    p.add_argument("target")
    p = sub.add_parser("check-claims", help="CI guard for one PR")
    p.add_argument("--pr", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None, gh: GitHub | None = None) -> int:
    args = build_parser().parse_args(argv)
    gh = gh or GhCli()
    root = repo_root()
    match args.command:
        case "register":
            return cmd_register(gh, root, args.name)
        case "whoami":
            print(require_team(root))
            return 0
        case "status":
            return cmd_status(gh, root)
        case "claim":
            return cmd_claim(gh, root, args.target, args.allow_unready)
        case "release":
            return cmd_release(gh, root, args.target)
        case "check-claims":
            return cmd_check_claims(gh, args.pr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

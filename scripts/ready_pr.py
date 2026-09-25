#!/usr/bin/env python3
"""Bring a PR up to date and mark it ready, the same way for every team.

One command replaces the by-hand checklist in ``docs/ways-of-working/git-workflow.md``
("Before marking a PR ready for review"). It never merges the PR; the owner does that.

Steps, in order (each one stops the run with a reason on failure):

1. The PR's branch is checked out here and the working tree is clean.
2. ``git fetch`` then ``git merge origin/main`` (a merge, not a rebase: no force-push, and
   the squash merge flattens it anyway). A conflict is resolved automatically **only** when
   every conflicted file is ``docs/STATUS.md`` or ``CHANGELOG.md`` and every conflict block
   is two sets of list bullets (both sides only added lines). Both sides are kept, ``main``'s
   first. Anything else aborts the merge and reports.
3. Fragment check (``scripts/fragments.py check``), and the PR does not edit the shared
   ``STATUS.md`` / ``CHANGELOG.md`` lists unless ``--allow-shared-files`` was given.
4. Local checks: ruff check, ruff format --check, mypy, pytest.
5. The PR body has no unticked template boxes and says ``Closes #<issue>`` for the branch's
   issue. Every specialist review the touched paths require (``quant-auditor``,
   ``safety-reviewer``) is mentioned in the PR body or a PR comment.
6. Push, wait for CI on **that exact commit**, then ``gh pr ready``.

Usage::

    uv run python scripts/ready_pr.py 69
    uv run python scripts/ready_pr.py 69 --dry-run            # stop before pushing
    uv run python scripts/ready_pr.py 70 --allow-shared-files # process PRs only
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

MAIN_REF = "origin/main"
SHARED_FILES = ("docs/STATUS.md", "CHANGELOG.md")
BULLET_RE = re.compile(r"^- \S")
UNCHECKED_RE = re.compile(r"^\s*- \[ \] (.*)$", re.MULTILINE)
CLOSES_RE = re.compile(r"\b(?:closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)
BRANCH_ISSUE_RE = re.compile(r"^[a-z]+/(\d+)-")
CONFLICT_RE = re.compile(
    r"^<{7}[^\n]*\n(?P<ours>.*?)(?:^\|{7}[^\n]*\n.*?)?^={7}\n(?P<theirs>.*?)^>{7}[^\n]*\n",
    re.MULTILINE | re.DOTALL,
)

# Paths that require a specialist review before the PR is ready. Mirrors agents.md; the
# names are the agents' file names under .claude/agents/.
QUANT_AUDITOR = "quant-auditor"
SAFETY_REVIEWER = "safety-reviewer"
QUANT_PREFIXES = (
    "src/tradepartner/store/",
    "src/tradepartner/adapters/prices",
    "src/tradepartner/adapters/filings",
    "src/tradepartner/adapters/fixture_",
    "src/tradepartner/adapters/alpaca_prices",
    "src/tradepartner/adapters/edgar.py",
    "src/tradepartner/calendar.py",
    "src/tradepartner/universe.py",
    "src/tradepartner/gap.py",
    "src/tradepartner/ingest.py",
    "src/tradepartner/backfill.py",
    "src/tradepartner/health.py",
    "src/tradepartner/signals/",
    "src/tradepartner/backtest/",
    "tests/lookahead/",
    "tests/fixtures/universe/",
)
SAFETY_PREFIXES = (
    "src/tradepartner/adapters/broker",
    "src/tradepartner/adapters/fake_broker",
    "src/tradepartner/adapters/alpaca_raw",
    "src/tradepartner/adapters/edgar_raw",
    "src/tradepartner/cli_record.py",
    "src/tradepartner/cli.py",
    "src/tradepartner/config.py",
    "src/tradepartner/exec/",
    "src/tradepartner/risk/",
    "src/tradepartner/llm/",
    ".env.example",
)
LOCAL_CHECKS: tuple[tuple[str, ...], ...] = (
    ("uv", "run", "ruff", "check", "."),
    ("uv", "run", "ruff", "format", "--check", "."),
    ("uv", "run", "mypy"),
    ("uv", "run", "python", "scripts/fragments.py", "check"),
    ("uv", "run", "pytest", "-q"),
)
CI_TIMEOUT_S = 25 * 60
CI_POLL_S = 20


class ReadyError(RuntimeError):
    """The PR is not ready; the message says what to do."""


@dataclass(frozen=True)
class Pr:
    number: int
    branch: str
    base: str
    draft: bool
    body: str
    comments: tuple[str, ...]


@dataclass(frozen=True)
class CheckRun:
    name: str
    status: str  # COMPLETED | IN_PROGRESS | QUEUED | ...
    conclusion: str  # SUCCESS | FAILURE | ... | "" while running


@dataclass(frozen=True)
class HeadChecks:
    sha: str
    runs: tuple[CheckRun, ...]


class Runner(Protocol):
    """Everything that touches git, gh or the shell. Faked in tests."""

    def git(self, *args: str) -> str: ...
    def git_ok(self, *args: str) -> bool: ...
    def run_check(self, cmd: Sequence[str]) -> bool: ...
    def read(self, path: str) -> str: ...
    def write(self, path: str, text: str) -> None: ...
    def pr(self, number: int) -> Pr: ...
    def head_checks(self, number: int) -> HeadChecks: ...
    def mark_ready(self, number: int) -> None: ...
    def sleep(self, seconds: float) -> None: ...


# ── pure logic ──────────────────────────────────────────────────────────────────


def resolve_append_conflicts(text: str) -> str | None:
    """Resolve conflict blocks where both sides only added list bullets.

    Returns the resolved text, ``main``'s lines (theirs) before the branch's (ours) in each
    block, or ``None`` when any block holds something other than bullets or blank lines.
    """
    if "<<<<<<<" not in text:
        return text
    ok = True

    def _sub(m: re.Match[str]) -> str:
        nonlocal ok
        ours = [ln for ln in m.group("ours").splitlines() if ln.strip()]
        theirs = [ln for ln in m.group("theirs").splitlines() if ln.strip()]
        if not all(BULLET_RE.match(ln) for ln in [*ours, *theirs]):
            ok = False
            return m.group(0)
        merged = theirs + [ln for ln in ours if ln not in theirs]
        return "\n".join(merged) + "\n"

    out = CONFLICT_RE.sub(_sub, text)
    if not ok or "<<<<<<<" in out or ">>>>>>>" in out:
        return None
    return out


def unchecked_boxes(body: str) -> list[str]:
    """Template checklist items still ``- [ ]`` in the PR body."""
    return [m.group(1).strip() for m in UNCHECKED_RE.finditer(body)]


def closed_issues(body: str) -> set[int]:
    return {int(n) for n in CLOSES_RE.findall(body)}


def issue_of_branch(branch: str) -> int | None:
    m = BRANCH_ISSUE_RE.match(branch)
    return int(m.group(1)) if m else None


def required_reviews(paths: Sequence[str]) -> set[str]:
    """Specialist reviews the touched paths require, per agents.md."""
    out: set[str] = set()
    for p in paths:
        if p.startswith(QUANT_PREFIXES):
            out.add(QUANT_AUDITOR)
        if p.startswith(SAFETY_PREFIXES):
            out.add(SAFETY_REVIEWER)
    return out


def missing_reviews(required: set[str], texts: Sequence[str]) -> list[str]:
    """Required reviews not mentioned in any of ``texts`` (PR body and comments)."""
    blob = "\n".join(texts).lower()
    return sorted(r for r in required if r not in blob)


def checks_state(checks: HeadChecks, sha: str) -> str:
    """``pending`` | ``success`` | ``failure`` for the CI on one commit.

    ``pending`` also covers "GitHub has not seen this commit yet" and "no runs reported
    yet": neither is green (git-workflow: CI must have run on the exact commit).
    """
    if checks.sha != sha or not checks.runs:
        return "pending"
    if any(r.status.upper() != "COMPLETED" for r in checks.runs):
        return "pending"
    if all(r.conclusion.upper() in {"SUCCESS", "NEUTRAL", "SKIPPED"} for r in checks.runs):
        return "success"
    return "failure"


def shared_list_edits(diff_names: Sequence[str]) -> list[str]:
    return [p for p in diff_names if p in SHARED_FILES]


# ── the flow ────────────────────────────────────────────────────────────────────


def ready(
    r: Runner,
    number: int,
    *,
    dry_run: bool = False,
    allow_shared_files: bool = False,
    wait: bool = True,
    timeout_s: int = CI_TIMEOUT_S,
    poll_s: int = CI_POLL_S,
) -> int:
    pr = r.pr(number)

    def say(msg: str) -> None:
        print(msg, flush=True)

    # 1. right branch, clean tree
    branch = r.git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != pr.branch:
        raise ReadyError(f"PR #{number} is on '{pr.branch}' but this checkout is on '{branch}'")
    if r.git("status", "--porcelain"):
        raise ReadyError("working tree is not clean; commit or stash first")
    say(f"PR #{number}: branch {branch}, base {pr.base}")

    # 2. up to date with main
    r.git("fetch", "-q", "origin")
    main_ref = f"origin/{pr.base}"
    if r.git_ok("merge-base", "--is-ancestor", main_ref, "HEAD"):
        say(f"already contains {main_ref}")
    else:
        _merge_main(r, main_ref, say)

    # 3. fragments and shared files
    touched = r.git("diff", "--name-only", f"{main_ref}...HEAD").splitlines()
    shared = shared_list_edits(touched)
    if shared and not allow_shared_files:
        raise ReadyError(
            f"this PR edits {', '.join(shared)}. Record your entries as fragments instead: "
            "`uv run python scripts/fragments.py add <issue> --slug <slug> --status ... "
            "--added ...` (teams.md, Shared files). Process PRs may pass --allow-shared-files."
        )

    # 4. local checks
    for cmd in LOCAL_CHECKS:
        say(f"$ {' '.join(cmd)}")
        if not r.run_check(cmd):
            raise ReadyError(f"local check failed: {' '.join(cmd)}")

    # 5. template, linked issue, specialist reviews
    boxes = unchecked_boxes(pr.body)
    if boxes:
        raise ReadyError(
            "PR body still has unticked boxes (tick them or write n/a and why): " + "; ".join(boxes)
        )
    issue = issue_of_branch(pr.branch)
    if issue is not None and issue not in closed_issues(pr.body):
        raise ReadyError(f"PR body must say `Closes #{issue}` (the branch's issue)")
    required = required_reviews(touched)
    missing = missing_reviews(required, [pr.body, *pr.comments])
    if missing:
        raise ReadyError(
            "run these reviews and record the result in the PR body or a comment: "
            + ", ".join(missing)
        )
    say(f"reviews required: {sorted(required) or 'none'}; all recorded")

    if dry_run:
        say("dry run: stopping before push")
        return 0

    # 6. push, wait for CI on this commit, mark ready
    sha = r.git("rev-parse", "HEAD")
    r.git("push", "origin", f"HEAD:{pr.branch}")
    say(f"pushed {sha[:7]}")
    if not wait:
        say("not waiting for CI (--no-wait); PR left as is")
        return 0
    state = _wait_for_ci(r, number, sha, timeout_s, poll_s, say)
    if state != "success":
        raise ReadyError(f"CI {state} on {sha[:7]}; fix and run again")
    if pr.draft:
        r.mark_ready(number)
        say(f"PR #{number} marked ready for review")
    else:
        say(f"PR #{number} was already ready; CI green on {sha[:7]}")
    return 0


def _merge_main(r: Runner, main_ref: str, say: Callable[[str], None]) -> None:
    say(f"merging {main_ref}")
    if r.git_ok("merge", "--no-edit", main_ref):
        return
    conflicted = r.git("diff", "--name-only", "--diff-filter=U").splitlines()
    others = [p for p in conflicted if p not in SHARED_FILES]
    if others:
        r.git("merge", "--abort")
        raise ReadyError(
            f"merge of {main_ref} conflicts in {', '.join(conflicted)}. Only STATUS/CHANGELOG "
            "append conflicts are resolved automatically; resolve these by hand, commit, "
            "and run again."
        )
    for path in conflicted:
        resolved = resolve_append_conflicts(r.read(path))
        if resolved is None:
            r.git("merge", "--abort")
            raise ReadyError(
                f"{path}: conflict is not two lists of added bullets; resolve by hand "
                "(keep both sides), commit, and run again."
            )
        r.write(path, resolved)
        r.git("add", path)
        say(f"resolved append conflict in {path} (kept both sides)")
    r.git("commit", "--no-edit")


def _wait_for_ci(
    r: Runner, number: int, sha: str, timeout_s: int, poll_s: int, say: Callable[[str], None]
) -> str:
    deadline = time.monotonic() + timeout_s
    say(f"waiting for CI on {sha[:7]} (up to {timeout_s // 60} min)")
    while True:
        state = checks_state(r.head_checks(number), sha)
        if state != "pending":
            say(f"CI {state}")
            return state
        if time.monotonic() >= deadline:
            return "timeout"
        r.sleep(poll_s)


# ── real runner ─────────────────────────────────────────────────────────────────


class ShellRunner:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _git(self, *args: str, check: bool) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=check, capture_output=True, text=True
        )

    def git(self, *args: str) -> str:
        try:
            return self._git(*args, check=True).stdout.strip()
        except subprocess.CalledProcessError as exc:
            raise ReadyError(f"git {' '.join(args[:2])} failed: {exc.stderr.strip()}") from None

    def git_ok(self, *args: str) -> bool:
        return self._git(*args, check=False).returncode == 0

    def run_check(self, cmd: Sequence[str]) -> bool:
        return subprocess.run(list(cmd), cwd=self.root, check=False).returncode == 0

    def read(self, path: str) -> str:
        return (self.root / path).read_text()

    def write(self, path: str, text: str) -> None:
        (self.root / path).write_text(text)

    def _gh(self, *args: str) -> str:
        try:
            return subprocess.run(
                ["gh", *args], cwd=self.root, check=True, capture_output=True, text=True
            ).stdout
        except FileNotFoundError:
            raise ReadyError("gh CLI not found; install it and run `gh auth login`") from None
        except subprocess.CalledProcessError as exc:
            raise ReadyError(f"gh {' '.join(args[:2])} failed: {exc.stderr.strip()}") from None

    def pr(self, number: int) -> Pr:
        raw = json.loads(
            self._gh(
                "pr",
                "view",
                str(number),
                "--json",
                "number,headRefName,baseRefName,isDraft,body,comments",
            )
        )
        return Pr(
            int(raw["number"]),
            str(raw["headRefName"]),
            str(raw["baseRefName"]),
            bool(raw["isDraft"]),
            str(raw.get("body") or ""),
            tuple(str(c.get("body", "")) for c in raw.get("comments", [])),
        )

    def head_checks(self, number: int) -> HeadChecks:
        raw = json.loads(
            self._gh("pr", "view", str(number), "--json", "headRefOid,statusCheckRollup")
        )
        runs = tuple(
            CheckRun(
                str(c.get("name") or c.get("context") or "?"),
                str(c.get("status") or ("COMPLETED" if c.get("conclusion") else "")),
                str(c.get("conclusion") or c.get("state") or ""),
            )
            for c in raw.get("statusCheckRollup") or []
        )
        return HeadChecks(str(raw["headRefOid"]), runs)

    def mark_ready(self, number: int) -> None:
        self._gh("pr", "ready", str(number))

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


# ── entry point ─────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ready_pr.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pr", type=int, help="pull request number (its branch must be checked out)")
    parser.add_argument("--dry-run", action="store_true", help="stop before pushing")
    parser.add_argument(
        "--allow-shared-files",
        action="store_true",
        help="let this PR edit STATUS.md / CHANGELOG.md directly (process PRs only)",
    )
    parser.add_argument("--no-wait", action="store_true", help="push but do not wait for CI")
    parser.add_argument("--timeout-min", type=int, default=CI_TIMEOUT_S // 60)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], check=True, capture_output=True, text=True
        ).stdout.strip()
    )
    try:
        return ready(
            ShellRunner(root),
            args.pr,
            dry_run=args.dry_run,
            allow_shared_files=args.allow_shared_files,
            wait=not args.no_wait,
            timeout_s=args.timeout_min * 60,
        )
    except ReadyError as exc:
        print(f"NOT READY: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

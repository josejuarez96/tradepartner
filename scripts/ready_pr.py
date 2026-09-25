#!/usr/bin/env python3
"""Bring a PR up to date and mark it ready, the same way for every team.

One command replaces the by-hand checklist in ``docs/ways-of-working/git-workflow.md``
("Before marking a PR ready for review"). It never merges the PR; the owner does that.

Steps, in order (each one stops the run with a reason on failure):

1. The PR's branch is checked out here and the working tree is clean.
2. ``git fetch`` then ``git merge origin/main`` (a merge, not a rebase: no force-push, and
   the squash merge flattens it anyway), with diff3 conflict markers. A conflict is resolved
   automatically **only** when every conflicted file is ``docs/STATUS.md`` or
   ``CHANGELOG.md`` and every conflict block is a pure insertion at one spot: the base
   section is empty and both sides hold only list bullets. Both sides are kept, ``main``'s
   first. Anything else (a line one side deleted or edited) aborts the merge and reports.
3. Fragment check (``scripts/fragments.py check``); the branch's issue has a status fragment
   (and a changelog fragment on ``feat/``/``fix/`` branches); and the PR adds no bullets to
   the shared lists ("## Done" in ``STATUS.md``, "[Unreleased]" in ``CHANGELOG.md``) unless
   it is a fold (it also deletes fragment files) or ``--allow-shared-files`` was given.
   Other STATUS sections ("Blocked", "Decisions needed") may be edited freely.
4. Local checks: ruff check, ruff format --check, mypy and the fragment check always;
   pytest only when the diff touches code, tests, scripts or dependencies (``src/``,
   ``tests/``, ``scripts/``, ``pyproject.toml``, ``uv.lock``). CI runs the full suite on
   every PR either way, so it stays the gate. ``--tests`` forces the local run,
   ``--no-tests`` skips it.
5. The PR body has no unticked template boxes and says ``Closes #<issue>`` for the branch's
   issue. Every specialist review the touched paths require (``quant-auditor``,
   ``safety-reviewer``) has a verdict line in a PR **comment** (not the body, which carries
   the template's own wording): ``quant-auditor: PASS`` or ``PASS WITH FIXES``.
6. Push, wait for CI on **that exact commit**, then ``gh pr ready``.

Usage::

    uv run python scripts/ready_pr.py 69
    uv run python scripts/ready_pr.py 69 --dry-run            # stop before pushing
    uv run python scripts/ready_pr.py 70 --allow-shared-files # process PRs only
    uv run python scripts/ready_pr.py 69 --tests              # force local pytest
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
    r"^<{7}[^\n]*\n(?P<ours>.*?)(?:^\|{7}[^\n]*\n(?P<base>.*?))?^={7}\n(?P<theirs>.*?)^>{7}[^\n]*\n",
    re.MULTILINE | re.DOTALL,
)
VERDICT_RE = re.compile(
    r"\A\s*(?P<agent>quant-auditor|safety-reviewer):\s*(?P<verdict>pass(?: with fixes)?|fail)\b",
    re.IGNORECASE,
)
CREDENTIAL_IN_URL_RE = re.compile(r"://[^/@\s]+@")
STATUS_LIST = "## Done"
CHANGELOG_LIST = "## [Unreleased]"
FRAGMENT_DIRS = ("docs/status.d/", "changelog.d/")

# Paths that require a specialist review before the PR is ready. The plan's per-task
# "Review:" field (docs/plans/*.md) is the authority; these prefixes mirror it plus the
# CLAUDE.md rule (data/backtests/signals -> quant-auditor; broker/orders/secrets/LLM inputs
# -> safety-reviewer). Modules that do not exist yet are listed so the rule is right when
# they appear. Widening a list needs no review; shrinking one is a safety-reviewer change.
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
    "src/tradepartner/config.py",
    "src/tradepartner/timeutil.py",
    "src/tradepartner/universe.py",
    "src/tradepartner/gap.py",
    "src/tradepartner/ingest.py",
    "src/tradepartner/backfill.py",
    "src/tradepartner/health.py",
    "src/tradepartner/signals/",
    "src/tradepartner/backtest/",
    "scripts/make_fixture_universe.py",
    "tests/lookahead/",
    "tests/fixtures/universe/",
)
SAFETY_PREFIXES = (
    "src/tradepartner/adapters/broker",
    "src/tradepartner/adapters/fake_broker",
    "src/tradepartner/adapters/alpaca_raw",
    "src/tradepartner/adapters/edgar_raw",
    "src/tradepartner/adapters/alpaca_prices",
    "src/tradepartner/adapters/edgar.py",
    "src/tradepartner/cli_record.py",
    "src/tradepartner/cli.py",
    "src/tradepartner/config.py",
    "src/tradepartner/ingest.py",
    "src/tradepartner/exec/",
    "src/tradepartner/risk/",
    "src/tradepartner/llm/",
    "scripts/ready_pr.py",
    "scripts/fragments.py",
    "scripts/no_push_to_main.sh",
    ".github/workflows/",
    ".claude/agents/",
    ".claude/skills/",
    "tests/fixtures/alpaca/",
    "tests/fixtures/edgar/",
    "docs/runbooks/",
    ".env.example",
    ".claude/settings.json",
    ".pre-commit-config.yaml",
    "pyproject.toml",
)
LOCAL_CHECKS: tuple[tuple[str, ...], ...] = (
    ("uv", "run", "ruff", "check", "."),
    ("uv", "run", "ruff", "format", "--check", "."),
    ("uv", "run", "mypy"),
    ("uv", "run", "python", "scripts/fragments.py", "check"),
)
PYTEST_CHECK: tuple[str, ...] = ("uv", "run", "pytest", "-q")
# A diff touching any of these runs pytest locally; anything else leaves it to CI.
TEST_TRIGGER_PREFIXES = ("src/", "tests/", "scripts/")
TEST_TRIGGER_FILES = ("pyproject.toml", "uv.lock")
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
    """Resolve diff3 conflict blocks that are pure insertions of list bullets.

    A block qualifies only when its base section is present and empty (neither side deleted
    or edited anything; both inserted at the same spot) and both sides hold only bullets.
    Returns the resolved text, ``main``'s lines (theirs) before the branch's (ours) in each
    block, or ``None`` when any block fails the test. Markers without a base section (the
    default conflict style) never qualify, since a deletion could hide in them.
    """
    if "<<<<<<<" not in text:
        return text
    ok = True

    def _sub(m: re.Match[str]) -> str:
        nonlocal ok
        base = m.group("base")
        ours = [ln for ln in m.group("ours").splitlines() if ln.strip()]
        theirs = [ln for ln in m.group("theirs").splitlines() if ln.strip()]
        if base is None or base.strip():
            ok = False
            return m.group(0)
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


def missing_reviews(required: set[str], comments: Sequence[str]) -> list[str]:
    """Required reviews whose latest verdict comment is not PASS / PASS WITH FIXES.

    A verdict is the **first line** of a PR comment, ``<agent>: PASS``, ``PASS WITH FIXES``
    or ``FAIL``; comments are read in order and the latest verdict per agent wins. The PR
    body does not count: the template itself names both agents there. In this solo repo
    every comment comes from the owner's account, so this is a process gate, not an
    authentication boundary.
    """
    latest: dict[str, str] = {}
    for c in comments:
        m = VERDICT_RE.match(c)
        if m:
            latest[m.group("agent").lower()] = m.group("verdict").lower()
    return sorted(r for r in required if not latest.get(r, "").startswith("pass"))


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


def list_bullets(text: str, heading: str) -> list[str]:
    """Bullets under ``heading`` (a ``## `` section) up to the next ``## `` heading."""
    out: list[str] = []
    inside = False
    for ln in text.splitlines():
        if ln.startswith("## "):
            inside = ln.strip() == heading
            continue
        if inside and BULLET_RE.match(ln):
            out.append(ln.rstrip())
    return out


def added_list_bullets(main_text: str, branch_text: str, heading: str) -> list[str]:
    """Bullets the branch adds under ``heading`` that ``main`` does not have."""
    have = set(list_bullets(main_text, heading))
    return [b for b in list_bullets(branch_text, heading) if b not in have]


def is_fold(diff_names: Sequence[str], deleted: Sequence[str]) -> bool:
    """A fold deletes fragment files while touching the shared files."""
    return any(p.startswith(FRAGMENT_DIRS) for p in deleted) and any(
        p in SHARED_FILES for p in diff_names
    )


def missing_fragments(branch: str, diff_names: Sequence[str]) -> list[str]:
    """Fragment files the branch's issue needs but the diff does not add."""
    issue = issue_of_branch(branch)
    if issue is None:
        return []
    need = [f"docs/status.d/{issue}-"]
    if branch.startswith(("feat/", "fix/")):
        need.append(f"changelog.d/{issue}-")
    return [f"{p}<slug>.md" for p in need if not any(d.startswith(p) for d in diff_names)]


def tests_needed(paths: Sequence[str]) -> bool:
    """Whether the diff can make pytest fail: it touches code, tests, scripts or deps."""
    return any(p.startswith(TEST_TRIGGER_PREFIXES) or p in TEST_TRIGGER_FILES for p in paths)


# ── the flow ────────────────────────────────────────────────────────────────────


def ready(
    r: Runner,
    number: int,
    *,
    dry_run: bool = False,
    allow_shared_files: bool = False,
    run_tests: bool | None = None,
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
    if pr.branch in {pr.base, "main", "HEAD"}:
        raise ReadyError(
            f"refusing to work on branch '{pr.branch}'; PRs come from feature branches"
        )
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

    # 3. fragments and shared lists
    touched = r.git("diff", "--name-only", f"{main_ref}...HEAD").splitlines()
    deleted = r.git("diff", "--name-only", "--diff-filter=D", f"{main_ref}...HEAD").splitlines()
    if not allow_shared_files:
        added: list[str] = []
        for path, heading in ((SHARED_FILES[0], STATUS_LIST), (SHARED_FILES[1], CHANGELOG_LIST)):
            if path in touched:
                main_text = (
                    r.git("show", f"{main_ref}:{path}")
                    if r.git_ok("cat-file", "-e", f"{main_ref}:{path}")
                    else ""
                )
                added += [
                    f"{path}: {b}" for b in added_list_bullets(main_text, r.read(path), heading)
                ]
        if added and not is_fold(touched, deleted):
            raise ReadyError(
                "this PR adds lines to the shared lists:\n  "
                + "\n  ".join(added)
                + "\nMove them into fragments (`uv run python scripts/fragments.py add <issue> "
                "--slug <slug> --status ... --added ...`) and remove only those lines from the "
                "shared files (teams.md, Shared files). A fold or a process PR may pass "
                "--allow-shared-files."
            )
        missing_frag = missing_fragments(pr.branch, touched)
        if missing_frag:
            raise ReadyError(
                "no fragment for this PR's issue: add "
                + " and ".join(missing_frag)
                + " with `uv run python scripts/fragments.py add <issue> --slug <slug> ...`"
            )

    # 4. local checks; pytest only when the diff can fail it (None = decide from the paths)
    checks = list(LOCAL_CHECKS)
    if run_tests is None:
        run_tests = tests_needed(touched)
        if not run_tests:
            say("skipping local pytest: no code, test, script or dependency changes; CI runs it")
    elif not run_tests:
        say("skipping local pytest (--no-tests); CI runs it")
    if run_tests:
        checks.append(PYTEST_CHECK)
    for cmd in checks:
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
    missing = missing_reviews(required, pr.comments)
    if missing:
        raise ReadyError(
            "run these reviews, address findings, then post a PR comment with a verdict line "
            "`<agent>: PASS` or `<agent>: PASS WITH FIXES` for each: " + ", ".join(missing)
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
    if r.git_ok("-c", "merge.conflictStyle=diff3", "merge", "--no-edit", main_ref):
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
        try:
            text = r.read(path)
        except FileNotFoundError:
            text = ""
        if "<<<<<<<" not in text:  # modify/delete or rename: nothing safe to do here
            r.git("merge", "--abort")
            raise ReadyError(
                f"{path}: conflict without markers (deleted or renamed on one side); "
                "resolve by hand, commit, and run again."
            )
        resolved = resolve_append_conflicts(text)
        if resolved is None:
            r.git("merge", "--abort")
            raise ReadyError(
                f"{path}: conflict is not a pure insertion of bullets on both sides (a line "
                "was deleted or edited); resolve by hand, commit, and run again."
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


def _redact(text: str) -> str:
    """Strip any ``user:token@`` from URLs in tool output before it is shown."""
    return CREDENTIAL_IN_URL_RE.sub("://***@", text.strip())


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
            raise ReadyError(f"git {' '.join(args[:2])} failed: {_redact(exc.stderr)}") from None

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
            raise ReadyError(f"gh {' '.join(args[:2])} failed: {_redact(exc.stderr)}") from None

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
    tests = parser.add_mutually_exclusive_group()
    tests.add_argument(
        "--tests",
        dest="tests",
        action="store_const",
        const=True,
        default=None,
        help="run pytest locally even if the diff touches no code, tests, scripts or deps",
    )
    tests.add_argument(
        "--no-tests",
        dest="tests",
        action="store_const",
        const=False,
        help="skip local pytest (CI still runs the full suite)",
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
            run_tests=args.tests,
            wait=not args.no_wait,
            timeout_s=args.timeout_min * 60,
        )
    except ReadyError as exc:
        print(f"NOT READY: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

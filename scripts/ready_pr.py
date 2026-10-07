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
3. Fragment check (``scripts/fragments.py check``); the branch's issue has its fragment
   (``changelog.d/<issue>-<slug>.md``, or the pre-#351 ``docs/status.d/`` one); and the PR
   adds no bullets to the shared lists ("## Recently done" in ``STATUS.md``,
   "[Unreleased]" in ``CHANGELOG.md``) unless
   it is a fold (it also deletes fragment files) or ``--allow-shared-files`` was given.
   Other STATUS sections ("Blocked", "Decisions needed") may be edited freely.
4. Local checks: ruff check, ruff format --check, mypy, the fragment check and
   ``tests/test_docs_budget.py`` always. No local pytest by default: CI runs the full suite
   on every diff that touches code, tests, scripts, dependencies or CI (``src/``,
   ``tests/``, ``scripts/``, ``.github/``, ``pyproject.toml``, ``uv.lock``,
   ``.python-version``; ``--tests-needed``) and on every push to main, sharded (#1113,
   #1130). ``--tests`` runs the test files the diff maps to (``targeted_tests``), or the
   full suite when the mapping is unclear (#456); ``--full-tests`` runs the full suite
   locally; ``--no-tests`` skips it.
5. The PR body has no unticked template boxes and says ``Closes #<issue>`` for the branch's
   issue. Every specialist review the touched paths require (``quant-auditor``,
   ``safety-reviewer``) has a verdict line in a PR **comment** (not the body, which carries
   the template's own wording), and the latest one is ``quant-auditor: PASS``. A
   ``PASS WITH FIXES`` needs a re-review after the fixes that posts ``PASS``.
6. Push, wait for CI on **that exact commit**, then ``gh pr ready``.

Usage::

    uv run python scripts/ready_pr.py 69
    uv run python scripts/ready_pr.py 69 --dry-run            # stop before pushing
    uv run python scripts/ready_pr.py 70 --allow-shared-files # process PRs only
    uv run python scripts/ready_pr.py 69 --tests              # force local pytest
    uv run python scripts/ready_pr.py 69 --full-tests         # full suite, not targeted
    git diff --name-only origin/main...HEAD | python3 scripts/ready_pr.py --tests-needed
                                                   # prints yes/no; CI gates its Tests step on it
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Collection, Mapping, Sequence
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
    r"\A[ \t]*(?P<agent>quant-auditor|safety-reviewer):(?P<verdict>[^\r\n]*)",
    re.IGNORECASE,
)
CREDENTIAL_IN_URL_RE = re.compile(r"://[^/@\s]+@")
STATUS_LIST = "## Recently done"
CHANGELOG_LIST = "## [Unreleased]"
FRAGMENT_DIRS = ("docs/status.d/", "changelog.d/")

# Paths that require a specialist review before the PR is ready. The plan's per-task
# "Review:" field (docs/plans/*.md) is the authority; these prefixes mirror it plus the
# CLAUDE.md rule (data/backtests/signals -> quant-auditor; broker/orders/secrets/LLM inputs
# -> safety-reviewer). Modules that do not exist yet are listed so the rule is right when
# they appear; tests/test_ready_pr.py names them in PLANNED_PREFIXES and fails on any other
# prefix missing from the tree, so a rename cannot silently disable the gate.
# Widening a list needs no review; shrinking one is a safety-reviewer change.
QUANT_AUDITOR = "quant-auditor"
SAFETY_REVIEWER = "safety-reviewer"
QUANT_PREFIXES = (
    "src/tradepartner/store/",
    "src/tradepartner/adapters/prices",
    "src/tradepartner/adapters/filings",
    "src/tradepartner/adapters/fixture_",
    "src/tradepartner/adapters/alpaca_prices",
    "src/tradepartner/adapters/edgar.py",
    "src/tradepartner/adapters/edgar_source.py",
    "src/tradepartner/calendar.py",
    "src/tradepartner/config.py",
    "src/tradepartner/timeutil.py",
    "src/tradepartner/universe.py",
    "src/tradepartner/gap.py",
    "src/tradepartner/ingest.py",
    "src/tradepartner/backfill.py",
    "src/tradepartner/health.py",
    "src/tradepartner/backtest/",
    "src/tradepartner/execution/",
    "scripts/make_fixture_universe.py",
    "tests/lookahead/",
    "tests/fixtures/universe/",
)
SAFETY_PREFIXES = (
    "src/tradepartner/adapters/broker",
    "src/tradepartner/adapters/fake_broker",
    "src/tradepartner/adapters/alpaca_raw",
    "src/tradepartner/adapters/alpaca_trading_raw",
    "src/tradepartner/adapters/alpaca_broker",
    "src/tradepartner/adapters/edgar_raw",
    "src/tradepartner/adapters/alpaca_prices",
    "src/tradepartner/adapters/edgar.py",
    "src/tradepartner/cli_record.py",
    "src/tradepartner/cli.py",
    "src/tradepartner/config.py",
    "src/tradepartner/ingest.py",
    "src/tradepartner/execution/",
    "src/tradepartner/errors.py",
    "src/tradepartner/llm/",
    "scripts/ready_pr.py",
    "tests/test_ready_pr.py",
    "scripts/fragments.py",
    "scripts/no_push_to_main.sh",
    "scripts/merge_train.py",
    "tests/test_merge_train.py",
    ".github/workflows/",
    ".github/rulesets/",
    ".claude/agents/",
    ".claude/skills/",
    "tests/fixtures/alpaca/",
    "tests/fixtures/edgar/",
    "docs/runbooks/",
    ".env.example",
    ".claude/settings.json",
    ".pre-commit-config.yaml",
    "pyproject.toml",
    "uv.lock",
)
LOCAL_CHECKS: tuple[tuple[str, ...], ...] = (
    ("uv", "run", "ruff", "check", "."),
    ("uv", "run", "ruff", "format", "--check", "."),
    ("uv", "run", "mypy"),
    ("uv", "run", "python", "scripts/fragments.py", "check"),
    ("uv", "run", "pytest", "-q", "tests/test_docs_budget.py"),
)
PYTEST_CHECK: tuple[str, ...] = ("uv", "run", "pytest", "-q")
# The full suite runs in parallel when pytest-xdist is installed (#581), as CI does. xdist
# workers each get their own subdirectory of any --basetemp, so that flag still works.
XDIST_ARGS: tuple[str, ...] = ("-n", "auto")
# Targeted local pytest (#456): the tests a diff maps to, or the full suite when the mapping
# is unclear. CI always runs the full suite, so a miss here is caught there, later.
FULL_SUITE_FILES = ("pyproject.toml", "uv.lock", ".python-version")
DOCS_BUDGET_TEST = "tests/test_docs_budget.py"
# Static checks that scan a whole subtree: any change under the prefix can fail them.
TREE_SCAN_TESTS: dict[str, tuple[str, ...]] = {
    "src/": (
        "tests/execution/test_boundaries.py",
        "tests/execution/test_sdk_boundary.py",
        "tests/test_no_forbidden_imports.py",
        "tests/test_no_literals.py",
    ),
    "src/tradepartner/backtest/": ("tests/backtest/test_store_provider.py",),
}
# A diff touching any of these runs pytest, locally and in CI on a PR; anything else skips it
# (pushes to main always run the full suite).
TEST_TRIGGER_PREFIXES = ("src/", "tests/", "scripts/", ".github/")
TEST_TRIGGER_FILES = ("pyproject.toml", "uv.lock", ".python-version")
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


def parallel_if_full_suite(cmd: Sequence[str], *, xdist: bool) -> tuple[str, ...]:
    """The full-suite pytest command with ``-n auto`` added when xdist is installed (#581).

    Targeted runs (pytest plus file paths) and every other command pass through unchanged.
    """
    if xdist and tuple(cmd) == PYTEST_CHECK:
        return (*cmd, *XDIST_ARGS)
    return tuple(cmd)


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
    """Required reviews whose latest verdict comment is not PASS.

    A verdict is the **first line** of a PR comment, ``<agent>: PASS``, ``PASS WITH FIXES``
    or ``FAIL``; comments are read in order and the latest verdict per agent wins. Only
    ``PASS`` passes: ``PASS WITH FIXES`` leaves SHOULD FIX findings open, so it counts once
    the re-review after the fixes posts a later ``PASS`` (#356). The whole rest of the first
    line is the verdict, so ``PASS (with fixes)`` or ``PASS / FAIL`` is not a pass. The PR
    body does not count: the template itself names both agents there. In this solo repo
    every comment comes from the owner's account, so this is a process gate, not an
    authentication boundary.
    """
    latest: dict[str, str] = {}
    for c in comments:
        m = VERDICT_RE.match(c)
        if m:
            latest[m.group("agent").lower()] = " ".join(m.group("verdict").split()).lower()
    return sorted(r for r in required if latest.get(r) != "pass")


def checks_state(checks: HeadChecks, sha: str) -> str:
    """``pending`` | ``success`` | ``failure`` for the CI on one commit.

    ``pending`` also covers "GitHub has not seen this commit yet", "no runs reported
    yet", and "the required ``checks`` run hasn't appeared in the rollup yet": none of
    those is green (git-workflow: CI must have run on the exact commit). The last case
    matters since #1112: ``checks`` now ``needs:`` other jobs, so GitHub does not create
    its check run until those finish, and a rollup can otherwise show every run so far
    (e.g. ``checks-fast``, ``claims``) green while the run that actually gates pytest
    hasn't started.
    """
    if checks.sha != sha or not checks.runs:
        return "pending"
    if not any(r.name == "checks" for r in checks.runs):
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
    """The fragment file the branch's issue needs but the diff does not add: one
    ``changelog.d/<issue>-<slug>.md`` (#351), or the pre-#351 ``docs/status.d/`` one."""
    issue = issue_of_branch(branch)
    if issue is None:
        return []
    have = tuple(f"{d}{issue}-" for d in FRAGMENT_DIRS)
    if any(d.startswith(have) for d in diff_names):
        return []
    return [f"changelog.d/{issue}-<slug>.md"]


def own_changelog_fragments(
    branch: str, diff_names: Sequence[str], deleted: Collection[str]
) -> list[str]:
    """The ``changelog.d/<issue>-*`` fragments of the branch's issue that the diff adds or
    edits (not deletes): the texts ``lacks_changelog_bullets`` reads. Shared with
    ``merge_train`` (#764) so both read the same files."""
    issue = issue_of_branch(branch)
    if issue is None:
        return []
    return [p for p in diff_names if p.startswith(f"changelog.d/{issue}-") and p not in deleted]


def lacks_changelog_bullets(branch: str, fragment_texts: Sequence[str]) -> bool:
    """On a ``feat/`` or ``fix/`` branch, whether none of the issue's added
    ``changelog.d`` fragments holds a CHANGELOG heading with a bullet (a STATUS-only
    fragment, or only an old ``docs/status.d`` one, does not record the change)."""
    if not branch.startswith(("feat/", "fix/")):
        return False
    for text in fragment_texts:
        heading = False
        for line in text.splitlines():
            if line.startswith("### "):
                heading = True
            elif heading and BULLET_RE.match(line):
                return False
    return True


def tests_needed(paths: Sequence[str]) -> bool:
    """Whether the diff can make pytest fail: it touches code, tests, scripts or deps."""
    return any(p.startswith(TEST_TRIGGER_PREFIXES) or p in TEST_TRIGGER_FILES for p in paths)


def _module_tests(path: str, test_sources: Mapping[str, str]) -> set[str]:
    """Test files that import the module at ``src/<dotted>.py``, by name."""
    dotted = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
    parent, _, stem = dotted.rpartition(".")
    direct = re.compile(rf"\b{re.escape(dotted)}\b")
    from_parent = re.compile(
        rf"\bfrom\s+{re.escape(parent)}\s+import\s+(?:\([^)]*|[^\n]*)\b{re.escape(stem)}\b"
    )
    return {
        test
        for test, text in test_sources.items()
        if direct.search(text) or from_parent.search(text)
    }


def targeted_tests(
    paths: Sequence[str], test_sources: Mapping[str, str], deleted: Collection[str] = ()
) -> tuple[str, ...] | None:
    """The test files a diff maps to, or ``None`` for the full suite (#456).

    ``test_sources`` maps every tracked ``tests/**/test_*.py`` path to its text. A changed
    test file runs itself; a ``src/`` module runs every test file that imports it by name plus
    the static checks over its subtree (``TREE_SCAN_TESTS``); a file under ``scripts/`` or
    ``.github/`` runs the tests that name it. Anything whose effect cannot be told falls back
    to the full suite: a ``conftest.py``, a dependency or Python-version file, a non-test file
    under ``tests/`` (fixtures, helpers), a package ``__init__``, a non-Python file under
    ``src/``, a deleted module, and a module or script no test names. A ``.github/`` file no
    test names, and docs, map to nothing. A test file not in ``test_sources`` (deleted or
    renamed away) is not run. ``tests/test_docs_budget.py`` is left out: it always runs on
    its own. Pass ``deleted`` from a ``--no-renames`` diff, so a moved module counts as gone.
    """
    selected: set[str] = set()
    for p in paths:
        name = p.rpartition("/")[2]
        if p in FULL_SUITE_FILES or name == "conftest.py":
            return None
        if p.startswith("tests/"):
            if not (name.startswith("test_") and name.endswith(".py")):
                return None
            selected.add(p)
        elif p.startswith("src/"):
            if p in deleted or not p.endswith(".py") or name == "__init__.py":
                return None
            found = _module_tests(p, test_sources)
            if not found:
                return None
            selected |= found
            for prefix, scans in TREE_SCAN_TESTS.items():
                if p.startswith(prefix):
                    selected.update(scans)
        elif p.startswith(("scripts/", ".github/")):
            mention = re.compile(rf"\b{re.escape(name)}\b")
            found = {t for t, text in test_sources.items() if mention.search(text)}
            if not found and p.startswith("scripts/"):
                return None
            selected |= found
    selected &= set(test_sources)
    selected.discard(DOCS_BUDGET_TEST)
    return tuple(sorted(selected))


# ── the flow ────────────────────────────────────────────────────────────────────


def ready(
    r: Runner,
    number: int,
    *,
    dry_run: bool = False,
    allow_shared_files: bool = False,
    run_tests: bool | None = None,
    full_tests: bool = False,
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
    # --no-renames: a file moved out of src/ must list its old path too.
    touched = r.git(
        "-c", "core.quotePath=false", "diff", "--no-renames", "--name-only", f"{main_ref}...HEAD"
    ).splitlines()
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
        issue = issue_of_branch(pr.branch)
        own = own_changelog_fragments(pr.branch, touched, deleted)
        if lacks_changelog_bullets(pr.branch, [r.read(p) for p in own]):
            raise ReadyError(
                "a feat/fix PR records its change in CHANGELOG: add a bullet to "
                f"changelog.d/{issue}-<slug>.md (`fragments.py add ... --added/--fixed ...`)"
            )

    # 4. local checks; pytest only when the diff can fail it (None = decide from the paths),
    # and then only the tests it maps to unless --full-tests (CI runs the full suite)
    checks = list(LOCAL_CHECKS)
    if full_tests:
        say("local pytest: the full suite (--full-tests)")
        checks.append(PYTEST_CHECK)
    elif run_tests is False:
        say("skipping local pytest (--no-tests); CI still runs it if the diff touches code")
    elif run_tests is None and tests_needed(touched):
        # CI runs the full suite on this diff in ~11 min (sharded, #1113); a local run of
        # the mapped tests took 40+ min for a config.py diff on the owner's Mac (#1130)
        say("skipping local pytest: CI runs the full suite on this diff (--tests forces it)")
    elif run_tests:
        sources = {
            p: r.read(p)
            for p in r.git("ls-files", "tests").splitlines()
            if p.rpartition("/")[2].startswith("test_") and p.endswith(".py")
        }
        # --no-renames: a module moved within src/ must count as deleted at its old path
        gone = r.git(
            "diff", "--no-renames", "--name-only", "--diff-filter=D", f"{main_ref}...HEAD"
        ).splitlines()
        selected = targeted_tests(touched, sources, gone)
        if selected is None or (run_tests and not selected):
            say("local pytest: the full suite (the diff's tests cannot be told from its paths)")
            checks.append(PYTEST_CHECK)
        elif selected:
            say(f"local pytest: {len(selected)} targeted file(s); CI runs the full suite")
            checks.append((*PYTEST_CHECK, *selected))
        else:
            say("skipping local pytest: no test maps to the diff; CI runs the full suite")
    else:
        say("skipping local pytest: no code, test, script, dependency or CI changes")
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
            "run these reviews, address findings and re-run them until the latest PR comment "
            "verdict line is `<agent>: PASS` (PASS WITH FIXES needs a re-review) for each: "
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
        cmd = parallel_if_full_suite(cmd, xdist=importlib.util.find_spec("xdist") is not None)
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
    parser.add_argument(
        "pr", type=int, nargs="?", help="pull request number (its branch must be checked out)"
    )
    parser.add_argument(
        "--tests-needed",
        action="store_true",
        help="read changed paths from stdin, print yes if pytest must run, else no (for CI)",
    )
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
        help="run the mapped tests locally (default: CI runs the full suite instead)",
    )
    tests.add_argument(
        "--no-tests",
        dest="tests",
        action="store_const",
        const=False,
        help="skip local pytest (CI still runs it when the diff touches code)",
    )
    tests.add_argument(
        "--full-tests",
        action="store_true",
        help="run the full pytest suite locally instead of the tests the diff maps to",
    )
    parser.add_argument("--no-wait", action="store_true", help="push but do not wait for CI")
    parser.add_argument("--timeout-min", type=int, default=CI_TIMEOUT_S // 60)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.tests_needed:
        paths = [line.strip() for line in sys.stdin if line.strip()]
        print("yes" if tests_needed(paths) else "no")
        return 0
    if args.pr is None:
        parser.error("the following arguments are required: pr")
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
            full_tests=args.full_tests,
            wait=not args.no_wait,
            timeout_s=args.timeout_min * 60,
        )
    except ReadyError as exc:
        print(f"NOT READY: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

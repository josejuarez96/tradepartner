"""Merge train: test a batch of ready PRs together, land the longest green prefix.

Spec: docs/specs/merge-train.md. This is the **pure half** (plan T72): the record
types and every rule the commands apply, with no I/O, so each is a tested function.
The commands (`build`, `merge`, `status`, `prune`) and their runner are T73/T74.

- `eligibility` is req 1: checks (0) to (i) in order, the first failure the one
  reason. Comments are filtered to the repository owner's login first, so a pasted
  verdict or a forged `merge-train:` line from another account is invisible; checks
  (e) to (i) reuse `ready_pr`'s pure functions by import, never a copy.
- `order_batch` is req 2; `classify_run` req 4; `next_probe`, `probe_branch` and
  `bisect_result` req 5; `mergeable_prefix` req 6; `record_matches_comments` req 7;
  `comment` req 8.
- Positions are 1-based: prefix k is the first k accepted PRs, and the PR at
  position k is `accepted[k - 1]`.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import ready_pr

#: The CI steps whose failure makes a run `red` (req 4), matched up to the first " (".
RED_STEPS = ("Hygiene", "Fragments", "Lint", "Format", "Types", "Tests")
#: A failed job shorter than this with no checked step is the infrastructure case.
INCONCLUSIVE_SECONDS = 60
#: Every train comment's first line starts with this.
COMMENT_PREFIX = "merge-train:"
OUTCOMES = ("TESTED", "MERGED", "DROPPED", "CULPRIT", "HELD", "INCONCLUSIVE", "INELIGIBLE")
PR_STATES = ("accepted", "dropped", "already_merged", "ineligible")

_TRAIN_LINE = re.compile(r"^merge-train: (?P<outcome>[A-Z]+) batch (?P<batch>\S+)\s*$")
_REQUIRED_CHECKS = ("checks", "claims")


# --- records (spec Data / interfaces) ------------------------------------------------------


@dataclass
class PrEntry:
    number: int
    head: str
    branch: str
    issue: int | None
    state: str
    reason: str | None = None
    paths: list[str] | None = None
    conflicts_with: list[int] | None = None


@dataclass
class Probe:
    k: int
    branch: str
    sha: str
    run_id: int | None
    run_attempt: int | None
    run_url: str | None
    outcome: str
    detail: str | None = None


@dataclass
class MergeLog:
    started_at: str | None = None
    merged: list[dict[str, Any]] = field(default_factory=list)
    stopped_at_position: int | None = None
    reason: str | None = None
    resumed_at: str | None = None


@dataclass
class Record:
    """`<record dir>/<batch id>.json`: everything `build` learned and `merge` needs."""

    batch_id: str
    built_at: str
    base: str
    main_green_at_base: bool | None
    requested: list[int] | str
    prs: list[PrEntry]
    trees: list[str]
    train_branch: str
    train_sha: str | None = None
    run_id: int | None = None
    run_attempt: int | None = None
    run_url: str | None = None
    outcome: str | None = None
    detail: str | None = None
    probes: list[Probe] = field(default_factory=list)
    green_prefixes: list[int] = field(default_factory=list)
    culprit: int | None = None
    held: list[int] = field(default_factory=list)
    merge: MergeLog | None = None
    pruned: bool = False

    def accepted(self) -> list[PrEntry]:
        return [p for p in self.prs if p.state == "accepted"]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> Record:
        raw = json.loads(text)
        raw["prs"] = [PrEntry(**p) for p in raw["prs"]]
        raw["probes"] = [Probe(**p) for p in raw["probes"]]
        raw["merge"] = None if raw["merge"] is None else MergeLog(**raw["merge"])
        return cls(**raw)


def batch_id(built_at: datetime, base: str) -> str:
    """`YYYYMMDD-HHMMSS-<base7>` from the UTC build time (spec Definitions)."""
    if built_at.utcoffset() != timedelta(0):
        raise ValueError(f"built_at must be tz-aware UTC, got {built_at!r}")
    return f"{built_at:%Y%m%d-%H%M%S}-{base[:7]}"


# --- req 1: eligibility -----------------------------------------------------------------


@dataclass(frozen=True)
class PullRequest:
    """What `build` reads of one PR from GitHub (never from a comment)."""

    number: int
    author: str
    head_repo: str
    repo: str
    state: str
    is_draft: bool
    base: str
    labels: tuple[str, ...]
    head: str
    branch: str
    body: str
    fragment_texts: tuple[str, ...] = ()
    title: str = ""

    @property
    def owner(self) -> str:
        return self.repo.split("/", 1)[0]


@dataclass(frozen=True)
class Comment:
    author: str
    body: str


def owner_bodies(comments: Sequence[Comment], owner: str) -> list[str]:
    """The bodies of the comments the owner's login wrote, in order (req 1)."""
    return [c.body for c in comments if c.author == owner]


def _train_line(body: str) -> tuple[str, str] | None:
    match = _TRAIN_LINE.match(body.split("\n", 1)[0])
    return (match["outcome"], match["batch"]) if match else None


def _head_line(body: str) -> str | None:
    """The SHA on a comment's exact `head <sha>` line, if it has one."""
    match = re.search(r"^head (\S+)$", body, re.MULTILINE)
    return match[1] if match else None


def eligibility(
    pr: PullRequest,
    head_checks: ready_pr.HeadChecks,
    diff_paths: Sequence[str],
    comments: Sequence[Comment],
) -> str | None:
    """The first failing check of req 1, as `(<label>) <why>`, or None (eligible)."""
    if pr.head_repo != pr.repo or pr.author != pr.owner:
        return "(0) not a branch of this repository by its owner"
    if pr.state.upper() != "OPEN" or pr.is_draft:
        return "(a) not open, or a draft"
    if pr.base != "main":
        return f"(b) base is {pr.base}, not main"
    if "parked" in pr.labels:
        return "(c) parked"
    bodies = owner_bodies(comments, pr.owner)
    train = [(line, b) for b in bodies if (line := _train_line(b)) and line[0] != "INELIGIBLE"]
    if train and train[-1][0][0] == "CULPRIT" and _head_line(train[-1][1]) == pr.head:
        return "(d) culprit at its current head"
    green = {r.name for r in head_checks.runs if r.conclusion.upper() == "SUCCESS"}
    if (
        not set(_REQUIRED_CHECKS) <= green
        or ready_pr.checks_state(head_checks, pr.head) != "success"
    ):
        return "(e) checks and claims did not both conclude success on the head"
    if unchecked := ready_pr.unchecked_boxes(pr.body):
        return f"(f) unticked box: {unchecked[0]}"
    issue = ready_pr.issue_of_branch(pr.branch)
    if issue is None:
        return f"(g) branch {pr.branch} names no issue"
    if issue not in ready_pr.closed_issues(pr.body):
        return f"(g) body does not say Closes #{issue}"
    missing = ready_pr.missing_fragments(pr.branch, diff_paths)
    if missing or ready_pr.lacks_changelog_bullets(pr.branch, pr.fragment_texts):
        return "(h) fragment missing, or no CHANGELOG bullet"
    if reviews := ready_pr.missing_reviews(ready_pr.required_reviews(diff_paths), bodies):
        return f"(i) review without a latest PASS: {', '.join(reviews)}"
    return None


# --- req 2: order ---------------------------------------------------------------------------


def order_batch(numbers: Sequence[int], explicit: Sequence[int] | None) -> list[int]:
    """Ascending PR number, or exactly the `--order` given (the same PRs)."""
    if len(set(numbers)) != len(numbers):
        raise ValueError(f"a PR is named twice: {list(numbers)}")
    if explicit is None:
        return sorted(numbers)
    if sorted(explicit) != sorted(numbers):
        raise ValueError(f"--order {list(explicit)} must name exactly {sorted(numbers)}")
    return list(explicit)


# --- req 4: run classification -----------------------------------------------------------


@dataclass(frozen=True)
class Step:
    name: str
    conclusion: str


@dataclass(frozen=True)
class Outcome:
    kind: str  # green | red | inconclusive
    detail: str | None = None


def _step_key(name: str) -> str:
    return name.split(" (", 1)[0]


def classify_run(
    job_steps: Sequence[Step], duration_s: float, status: str | None, conclusion: str | None
) -> Outcome:
    """Classify the `checks` job of one run (req 4). `status` None: no run appeared."""
    if status is None:
        return Outcome("inconclusive", "no run appeared")
    if status.lower() != "completed":
        return Outcome("inconclusive", "the run timed out (not completed)")
    result = (conclusion or "").lower()
    if result == "success":
        return Outcome("green")
    failed = [_step_key(s.name) for s in job_steps if s.conclusion.lower() == "failure"]
    red = [name for name in failed if name in RED_STEPS]
    if result == "failure" and red:
        return Outcome("red", f"{red[0]} failed")
    if result != "failure":
        return Outcome("inconclusive", f"the run was {result or 'not concluded'} (cancelled?)")
    if failed:
        return Outcome("inconclusive", f"{failed[0]} failed")
    if duration_s < INCONCLUSIVE_SECONDS:
        return Outcome("inconclusive", f"failed in under {INCONCLUSIVE_SECONDS} s with no steps")
    return Outcome("inconclusive", "failed before the checked steps")


# --- req 5: bisect --------------------------------------------------------------------------


def probe_branch(batch: str, k: int) -> str:
    """The sibling branch a probe of prefix k runs on (spec Definitions)."""
    return f"train/{batch}-p{k}"


def next_probe(green_k: int, red_k: int) -> int | None:
    """The prefix to probe next, or None once the bounds are adjacent."""
    return None if red_k - green_k <= 1 else (green_k + red_k) // 2


def bisect_result(green_k: int, red_k: int, accepted: Sequence[int]) -> tuple[int, list[int]]:
    """(culprit, held): the PR at position `red_k` and every PR after it."""
    if not 0 <= green_k < red_k <= len(accepted) or red_k - green_k != 1:
        raise ValueError(f"bounds {green_k}, {red_k} not adjacent within 0..{len(accepted)}")
    return accepted[red_k - 1], list(accepted[red_k:])


# --- req 6: what may merge ---------------------------------------------------------------


def mergeable_prefix(record: Record, still_valid: Sequence[bool]) -> int:
    """The longest green prefix whose PRs all still verify (0: merge nothing).
    `still_valid` holds one verdict per accepted PR, in order; any other length is
    refused, so a PR nobody checked can never count as verified."""
    if len(still_valid) != len(record.accepted()):
        raise ValueError(f"{len(still_valid)} verdicts for {len(record.accepted())} accepted PRs")
    if any(not 1 <= k <= len(still_valid) for k in record.green_prefixes):
        raise ValueError(f"green prefixes {record.green_prefixes} outside 1..{len(still_valid)}")
    return max((k for k in record.green_prefixes if all(still_valid[:k])), default=0)


# --- req 7: the record against the owner's TESTED comments ---------------------------------


def _parse_tested(body: str) -> tuple[str, int, str, frozenset[int]] | None:
    line = _train_line(body)
    if line is None or line[0] != "TESTED":
        return None
    fields = dict(re.findall(r"^(position|head) (\S+)$", body, re.MULTILINE))
    prefixes = frozenset(int(k) for k in re.findall(r"^prefix (\d+) ", body, re.MULTILINE))
    if set(fields) != {"position", "head"}:
        return None
    return line[1], int(fields["position"]), fields["head"], prefixes


def record_matches_comments(record: Record, owner_comments: Mapping[int, Sequence[str]]) -> bool:
    """Whether the frozen record is the one `build` reported (req 7). `owner_comments`
    maps each PR number to the bodies its owner-authored comments carry."""
    accepted = record.accepted()
    numbers = [pr.number for pr in record.prs]
    if len(set(numbers)) != len(numbers):
        return False  # a PR listed twice
    if any(not 1 <= g <= len(accepted) for g in record.green_prefixes):
        return False  # a prefix longer than the accepted list: a PR was removed
    longest = max(record.green_prefixes, default=0)
    position = {pr.number: k for k, pr in enumerate(accepted, start=1)}
    listed: set[int] = set()
    for pr in record.prs:
        tested = [
            t
            for body in owner_comments.get(pr.number, ())
            if (t := _parse_tested(body)) and t[0] == record.batch_id
        ]
        k = position.get(pr.number)
        if k is None or k > longest:
            if tested:
                return False
            continue
        expected = frozenset(g for g in record.green_prefixes if g >= k)
        if not any(t[1:] == (k, pr.head, expected) for t in tested):
            return False
        listed |= expected
    return listed == set(record.green_prefixes)


# --- req 8: comments -------------------------------------------------------------------


def comment(outcome: str, batch: str, **detail: Any) -> str:
    """One train comment: `merge-train: <OUTCOME> batch <id>`, then its body."""
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")
    lines = [f"{COMMENT_PREFIX} {outcome} batch {batch}"]
    if outcome == "TESTED":
        lines += [f"position {detail['position']}", f"head {detail['head']}"]
        prefixes: Mapping[int, str] = detail["prefixes"]
        lines += [f"prefix {k} {prefixes[k]}" for k in sorted(prefixes)]
        return "\n".join(lines)
    if outcome == "CULPRIT":
        if not detail.get("head"):
            raise ValueError("a CULPRIT comment needs the PR's head SHA")
        lines.append(f"head {detail.pop('head')}")  # read back exactly by check (d)
    if outcome == "INCONCLUSIVE":
        reason = str(detail.pop("reason"))
        uv_lock = [int(n) for n in detail.pop("uv_lock_prs", ())]
        # a bisect-probe inconclusive stop (#524, follow-up 5) leaves `record.outcome` red,
        # which `build --resume` refuses ("nothing to resume"); advise a fresh `build` there
        # instead of the usual `--resume` (SHOULD FIX 6, pass-1 review of #640).
        rerun = (
            "Check GitHub Actions, then rerun `build` for a fresh batch: "
            "uv run python scripts/merge_train.py build"
            if detail.pop("advise_fresh_build", False)
            else "Check GitHub Actions, then rerun: "
            f"uv run python scripts/merge_train.py build --resume {batch}"
        )
        lines += [reason, rerun]
        if reason.startswith("Install") and len(uv_lock) >= 2:
            names = ", ".join(f"#{n}" for n in uv_lock)
            lines.append(
                f"{names} all touch uv.lock, which --resume cannot clear: "
                "drop one of them from the next build"
            )
    lines += [f"{key}: {value}" for key, value in detail.items()]
    return "\n".join(lines)


# --- the runner and `build` (plan T72b) -----------------------------------------------
#
# Everything below touches git, gh or the filesystem. The `Runner` protocol is the only
# seam: every rule above stays pure and tested on a fake runner; `ShellRunner` is a thin
# wrapper with no rule of its own. `build` is req 3 (the train branch), req 4 (one CI run,
# classified, with the confirming rerun of a red tree), req 5 (the main-green-at-base
# precondition and the bisect) and req 8 (comments, the record, the report). `merge`,
# `status` and `prune` are T72c.

DEFAULT_TIMEOUT_S = 60 * 60
DEFAULT_POLL_S = 30
MERGE_TRAIN_DIR_VAR = "TRADEPARTNER_MERGE_TRAIN_DIR"


class StoppedError(RuntimeError):
    """An early stop; the message becomes the run's final `STOPPED:` line."""


def _redact(text: str) -> str:
    """Strip any `user:token@` from URLs before showing or posting it: `ready_pr`'s own
    regex, reused (not copied) so the two can never drift apart."""
    return ready_pr.CREDENTIAL_IN_URL_RE.sub("://***@", text.strip())


@dataclass(frozen=True)
class PrData:
    """Everything `build` reads about one PR to decide eligibility and build with it."""

    pr: PullRequest
    comments: tuple[Comment, ...]
    head_checks: ready_pr.HeadChecks
    diff_paths: tuple[str, ...]


@dataclass(frozen=True)
class MergeAttempt:
    """What `gh pr merge --squash --match-head-commit` answered for one PR (req 6)."""

    outcome: str  # merged | retryable | failed
    detail: str = ""


@dataclass(frozen=True)
class RunInfo:
    """One CI run as `build` sees it while polling (`status` None: no run found yet)."""

    run_id: int | None
    run_attempt: int
    status: str | None
    conclusion: str | None
    url: str | None
    job_steps: tuple[Step, ...] = ()
    duration_s: float = 0.0


class Runner(Protocol):
    """Everything that touches git, gh or the filesystem. Faked in tests."""

    def main_sha(self) -> str: ...
    def open_pr_numbers(self) -> list[int]: ...
    def pr_data(self, number: int) -> PrData: ...
    def add_worktree(self, path: Path, base: str) -> None: ...
    def remove_worktree(self, path: Path) -> None: ...
    def merge_squash(self, worktree: Path, head: str) -> bool: ...
    def commit_squash(self, worktree: Path, message: str) -> str: ...
    def abort_merge(self, worktree: Path) -> None: ...
    def conflicted_paths(self, worktree: Path) -> list[str]: ...
    def is_ancestor(self, maybe_ancestor: str, ref: str) -> bool: ...
    def head_sha(self, worktree: Path) -> str: ...
    def tree_of(self, worktree: Path) -> str: ...
    def push(self, worktree: Path, ref: str, branch: str) -> None: ...
    def delete_branch(self, branch: str) -> bool: ...
    def find_run(self, sha: str, branch: str) -> RunInfo: ...
    def rerun(self, run_id: int) -> None: ...
    def post_comment(self, number: int, text: str) -> None: ...
    def sleep(self, seconds: float) -> None: ...
    def now(self) -> datetime: ...
    def tree_of_ref(self, ref: str) -> str: ...
    def parent_of(self, sha: str) -> str: ...
    def merge_pr(self, number: int, head: str) -> MergeAttempt: ...


# -- the record directory and worktree (spec Data / interfaces) -------------------------


def record_dir() -> Path:
    override = os.environ.get(MERGE_TRAIN_DIR_VAR)
    return Path(override) if override else Path.home() / ".tradepartner" / "merge_train"


def record_path(batch: str) -> Path:
    return record_dir() / f"{batch}.json"


def worktree_path(batch: str) -> Path:
    return record_dir() / f"worktree-{batch}"


def _save(record: Record) -> None:
    """Write the record atomically: never a half-written file for a concurrent reader."""
    record_dir().mkdir(parents=True, exist_ok=True)
    path = record_path(record.batch_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(record.to_json())
    tmp.replace(path)


# -- req 4: wait, classify, confirm a red tree with one rerun ---------------------------


def _wait_for_run(
    r: Runner,
    sha: str,
    branch: str,
    timeout_s: int,
    poll_s: int,
    say: Callable[[str], None],
    min_attempt: int = 0,
) -> RunInfo:
    """Wait for the run on `sha` (pushed to `branch`) to complete, at an attempt strictly
    after `min_attempt` (Red tree, req 4): a confirming rerun must never read the attempt
    it reran, even if GitHub still reports that one as `completed` for a few seconds."""
    deadline = time.monotonic() + timeout_s
    say(f"waiting for the run on {sha[:7]} (up to {timeout_s // 60} min)")
    info = r.find_run(sha, branch)
    while (
        info.status is None or info.status.lower() != "completed" or info.run_attempt <= min_attempt
    ):
        if time.monotonic() >= deadline:
            return info
        r.sleep(poll_s)
        info = r.find_run(sha, branch)
    return info


def _classified(
    r: Runner, sha: str, branch: str, timeout_s: int, poll_s: int, say: Callable[[str], None]
) -> tuple[Outcome, RunInfo]:
    """One run on `sha`, confirmed by a rerun when it comes back red (Red tree, req 4)."""
    info = _wait_for_run(r, sha, branch, timeout_s, poll_s, say)
    outcome = classify_run(info.job_steps, info.duration_s, info.status, info.conclusion)
    if outcome.kind != "red":
        return outcome, info
    say(f"{sha[:7]} red once; confirming with a rerun before it counts")
    if info.run_id is not None:
        r.rerun(info.run_id)
    info2 = _wait_for_run(r, sha, branch, timeout_s, poll_s, say, min_attempt=info.run_attempt)
    outcome2 = classify_run(info2.job_steps, info2.duration_s, info2.status, info2.conclusion)
    return outcome2, info2


def _main_green_at_base(
    r: Runner, base: str, timeout_s: int, poll_s: int, say: Callable[[str], None]
) -> tuple[bool | None, str | None]:
    """req 5 precondition: `main`'s latest run at `base`, waited for if still in progress."""
    info = _wait_for_run(r, base, "main", timeout_s, poll_s, say)
    if info.status is None or info.status.lower() != "completed":
        return None, "main unfinished at base"
    if (info.conclusion or "").lower() == "success":
        return True, None
    return False, "main is red at base"


# -- req 3: the train branch --------------------------------------------------------------


def _build_train_branch(
    r: Runner, worktree: Path, base: str, prs: list[PrData]
) -> tuple[list[PrEntry], list[str], list[str]]:
    """Sequential squash merges from `base`. Returns (entries, trees, commits), each
    `commits[k]`/`trees[k]` the k-th commit's SHA and tree (`k = 0`: `base` itself)."""
    entries: list[PrEntry] = []
    trees = [r.tree_of(worktree)]
    commits = [r.head_sha(worktree)]
    accepted_so_far: list[PrData] = []
    for data in prs:
        pr = data.pr
        issue = ready_pr.issue_of_branch(pr.branch)
        if r.is_ancestor(pr.head, base):
            entries.append(PrEntry(pr.number, pr.head, pr.branch, issue, "already_merged"))
            continue
        if r.merge_squash(worktree, pr.head):
            sha = r.commit_squash(worktree, pr.title or f"PR #{pr.number}")
            entries.append(PrEntry(pr.number, pr.head, pr.branch, issue, "accepted"))
            trees.append(r.tree_of(worktree))
            commits.append(sha)
            accepted_so_far.append(data)
        else:
            conflicted = sorted(r.conflicted_paths(worktree))
            r.abort_merge(worktree)
            conflicts_with = [
                d.pr.number for d in accepted_so_far if set(d.diff_paths) & set(conflicted)
            ]
            entries.append(
                PrEntry(
                    pr.number,
                    pr.head,
                    pr.branch,
                    issue,
                    "dropped",
                    reason="conflict",
                    paths=conflicted,
                    conflicts_with=conflicts_with,
                )
            )
    return entries, trees, commits


# -- req 5: the bisect over prefixes -------------------------------------------------------


def _run_bisect(
    r: Runner,
    record: Record,
    accepted: list[PrEntry],
    worktree: Path,
    commits: list[str],
    timeout_s: int,
    poll_s: int,
    say: Callable[[str], None],
) -> None:
    n = len(accepted)
    green_k, red_k = 0, n
    greens: list[int] = []
    probes: list[Probe] = []
    while (k := next_probe(green_k, red_k)) is not None:
        branch = probe_branch(record.batch_id, k)
        sha = commits[k]
        r.push(worktree, sha, branch)
        outcome, info = _classified(r, sha, branch, timeout_s, poll_s, say)
        probes.append(
            Probe(
                k,
                branch,
                sha,
                info.run_id,
                info.run_attempt,
                info.url,
                outcome.kind,
                outcome.detail,
            )
        )
        record.probes = probes
        _save(record)
        if outcome.kind == "green":
            greens.append(k)
            green_k = k
        elif outcome.kind == "red":
            red_k = k
        else:
            say(f"probe {k} inconclusive; stopping the bisect with the bounds found so far")
            break
    record.green_prefixes = sorted(greens)
    if red_k - green_k == 1:
        culprit, held = bisect_result(green_k, red_k, [p.number for p in accepted])
        record.culprit, record.held = culprit, held
    elif probes and probes[-1].outcome == "inconclusive":
        # the bisect itself stopped on an inconclusive probe (#524, follow-up 5): every PR
        # beyond the last green prefix is untested, not just noted in the record's detail.
        record.detail = f"bisect stopped: probe {probes[-1].k} inconclusive"
        record.held = [p.number for p in accepted[green_k:]]
    _save(record)
    _report_bisect(r, record, accepted, say)


# -- req 8: the comments ------------------------------------------------------------------


def _green_prefix_urls(record: Record, total: int) -> dict[int, str]:
    out: dict[int, str] = {}
    if record.outcome == "green" and total in record.green_prefixes:
        out[total] = record.run_url or ""
    for p in record.probes:
        if p.outcome == "green":
            out[p.k] = p.run_url or ""
    return out


def _post(r: Runner, say: Callable[[str], None], number: int, text: str) -> None:
    """Post one train comment, and print the same text too (req 8: "the same text is
    printed and written to the record" - the full body, not just its first line, #524).
    `say` is assumed to redact already (`run_build`'s wrapper)."""
    say(f"#{number}:\n{text}")
    r.post_comment(number, text)


def _report_tested(
    r: Runner, record: Record, accepted: list[PrEntry], say: Callable[[str], None]
) -> None:
    prefixes = _green_prefix_urls(record, len(accepted))
    for idx, pr in enumerate(accepted, start=1):
        at_or_above = {k: url for k, url in prefixes.items() if k >= idx}
        if at_or_above:
            _post(
                r,
                say,
                pr.number,
                comment(
                    "TESTED", record.batch_id, position=idx, head=pr.head, prefixes=at_or_above
                ),
            )


def _report_bisect(
    r: Runner, record: Record, accepted: list[PrEntry], say: Callable[[str], None]
) -> None:
    _report_tested(r, record, accepted, say)
    if record.culprit is not None:
        culprit_entry = next(p for p in accepted if p.number == record.culprit)
        green_before = max(record.green_prefixes, default=0)
        probe_url = next((p.run_url for p in record.probes if p.k == green_before + 1), None)
        _post(
            r,
            say,
            record.culprit,
            comment(
                "CULPRIT",
                record.batch_id,
                head=culprit_entry.head,
                prefix=green_before,
                probe=probe_url or "",
            ),
        )
        for number in record.held:
            _post(
                r,
                say,
                number,
                comment("HELD", record.batch_id, reason=f"after the culprit #{record.culprit}"),
            )
    elif record.held:
        # the bisect itself stopped on an inconclusive probe, with no culprit found (#524,
        # follow-up 5): every PR beyond the last green prefix is untested, not a HELD-after-
        # a-culprit PR, so it gets the INCONCLUSIVE comment req 8 names for that case.
        for number in record.held:
            _post(
                r,
                say,
                number,
                comment(
                    "INCONCLUSIVE",
                    record.batch_id,
                    reason=record.detail or "bisect inconclusive",
                    advise_fresh_build=True,
                ),
            )


# -- `build` --------------------------------------------------------------------------------


def run_build(
    r: Runner,
    requested: Sequence[int] | None,
    explicit_order: Sequence[int] | None,
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    poll_s: int = DEFAULT_POLL_S,
    say: Callable[[str], None] = print,
) -> Record:
    report = say
    say = lambda msg: report(_redact(msg))  # noqa: E731 - redact before anything is shown
    base = r.main_sha()
    built_at = r.now()
    bid = batch_id(built_at, base)
    if record_path(bid).exists():
        raise StoppedError(f"batch {bid} already has a record; wait a second and retry")
    wpath = worktree_path(bid)
    if wpath.exists():
        raise StoppedError(f"worktree {wpath} already exists; run prune or remove it by hand")

    numbers = list(requested) if requested is not None else r.open_pr_numbers()
    ordered_numbers = order_batch(numbers, explicit_order)
    index_of = {n: i for i, n in enumerate(ordered_numbers)}
    entries: list[PrEntry | None] = [None] * len(ordered_numbers)
    eligible_data: list[PrData] = []
    for number in ordered_numbers:
        data = r.pr_data(number)
        reason = eligibility(data.pr, data.head_checks, data.diff_paths, data.comments)
        if reason is None:
            eligible_data.append(data)
            continue
        issue = ready_pr.issue_of_branch(data.pr.branch)
        entries[index_of[number]] = PrEntry(
            number, data.pr.head, data.pr.branch, issue, "ineligible", reason=reason
        )
        if requested is not None:
            _post(r, say, number, comment("INELIGIBLE", bid, reason=reason))
        else:
            say(f"#{number} ineligible: {reason}")

    record = Record(
        batch_id=bid,
        built_at=built_at.isoformat(),
        base=base,
        main_green_at_base=None,
        requested=list(requested) if requested is not None else "all",
        prs=[],
        trees=[],
        train_branch=f"train/{bid}",
    )
    if not eligible_data:
        record.prs = [e for e in entries if e is not None]
        record.outcome, record.detail = "inconclusive", "nothing eligible to build"
        _save(record)
        return record

    r.add_worktree(wpath, base)
    try:
        built_entries, trees, commits = _build_train_branch(r, wpath, base, eligible_data)
        for data, entry in zip(eligible_data, built_entries, strict=True):
            entries[index_of[data.pr.number]] = entry
        record.prs = [e for e in entries if e is not None]
        record.trees = trees
        for entry in built_entries:
            if entry.state == "dropped":
                _post(r, say, entry.number, _dropped_comment(bid, entry))
        accepted = record.accepted()
        if not accepted:
            record.outcome, record.detail = "inconclusive", "every PR dropped or already merged"
            _save(record)
            return record
        data_by_number = {d.pr.number: d for d in eligible_data}
        uv_lock_prs = [
            p.number for p in accepted if "uv.lock" in data_by_number[p.number].diff_paths
        ]

        sha = r.head_sha(wpath)
        r.push(wpath, "HEAD", record.train_branch)
        record.train_sha = sha
        _save(record)

        main_green, detail = _main_green_at_base(r, base, timeout_s, poll_s, say)
        record.main_green_at_base = main_green
        if main_green is not True:
            record.outcome, record.detail = "inconclusive", detail
            _save(record)
            for pr in accepted:
                _post(r, say, pr.number, comment("INCONCLUSIVE", bid, reason=detail or ""))
            return record

        outcome, info = _classified(r, sha, record.train_branch, timeout_s, poll_s, say)
        record.run_id, record.run_attempt, record.run_url = info.run_id, info.run_attempt, info.url
        record.outcome, record.detail = outcome.kind, outcome.detail
        _save(record)

        if outcome.kind == "inconclusive":
            for pr in accepted:
                _post(
                    r,
                    say,
                    pr.number,
                    comment(
                        "INCONCLUSIVE", bid, reason=outcome.detail or "", uv_lock_prs=uv_lock_prs
                    ),
                )
            return record
        if outcome.kind == "green":
            record.green_prefixes = [len(accepted)]
            _save(record)
            _report_tested(r, record, accepted, say)
            return record

        _run_bisect(r, record, accepted, wpath, commits, timeout_s, poll_s, say)
        return record
    finally:
        r.remove_worktree(wpath)


def _dropped_comment(bid: str, entry: PrEntry) -> str:
    return comment(
        "DROPPED",
        bid,
        paths=", ".join(entry.paths or []),
        conflicts_with=", ".join(f"#{n}" for n in entry.conflicts_with or []),
    )


def run_build_resume(
    r: Runner,
    bid: str,
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    poll_s: int = DEFAULT_POLL_S,
    say: Callable[[str], None] = print,
) -> Record:
    """Re-attach to the recorded branch and run instead of rebuilding (req 4, AC7)."""
    report = say
    say = lambda msg: report(_redact(msg))  # noqa: E731 - redact before anything is shown
    path = record_path(bid)
    if not path.exists():
        raise StoppedError(f"no record for batch {bid}; run build without --resume")
    record = Record.from_json(path.read_text())
    if record.outcome != "inconclusive" or record.train_sha is None:
        raise StoppedError(f"batch {bid} is not inconclusive; nothing to resume")
    accepted = record.accepted()
    outcome, info = _classified(r, record.train_sha, record.train_branch, timeout_s, poll_s, say)
    record.run_id, record.run_attempt, record.run_url = info.run_id, info.run_attempt, info.url
    record.outcome, record.detail = outcome.kind, outcome.detail
    _save(record)
    if outcome.kind == "green":
        record.green_prefixes = [len(accepted)]
        _save(record)
        _report_tested(r, record, accepted, say)
    elif outcome.kind == "red":
        raise StoppedError(
            f"batch {bid} came back red on resume; its worktree is gone, so it cannot be "
            "bisected here: rerun build for a fresh batch"
        )
    else:
        uv_lock_prs = [p.number for p in accepted if "uv.lock" in r.pr_data(p.number).diff_paths]
        for pr in accepted:
            _post(
                r,
                say,
                pr.number,
                comment("INCONCLUSIVE", bid, reason=outcome.detail or "", uv_lock_prs=uv_lock_prs),
            )
    return record


# -- `merge`, `status`, `prune` (plan T72c) --------------------------------------------------
#
# `merge` is the only command that lands anything (reqs 6 and 7). It needs no worktree: the
# tree checks read `origin/main^{tree}` after a fetch (`main_sha`) in the clone it ran in.

MERGE_RETRY_ATTEMPTS = 5
MERGE_RETRY_DELAY_S = 30  # five attempts over about two minutes (four 30s waits)
MERGE_PR_TIMEOUT_S = 120  # #642, follow-up 3: `gh pr merge` must never hang indefinitely


def _merge_with_retries(
    r: Runner, number: int, head: str, say: Callable[[str], None]
) -> MergeAttempt:
    attempt = MergeAttempt("retryable", "never attempted")
    for i in range(MERGE_RETRY_ATTEMPTS):
        attempt = r.merge_pr(number, head)
        if attempt.outcome != "retryable":
            return attempt
        if i < MERGE_RETRY_ATTEMPTS - 1:
            say(f"#{number} not yet mergeable ({attempt.detail}); retrying")
            r.sleep(MERGE_RETRY_DELAY_S)
    return attempt


@contextlib.contextmanager
def _merge_lock() -> Iterator[None]:
    """An exclusive, non-blocking lock over all of `run_merge` (pass-1 review of #640,
    SHOULD FIX 3/7): two concurrent `merge` runs on this machine must never interleave
    their checks and their `gh pr merge` calls."""
    record_dir().mkdir(parents=True, exist_ok=True)
    with open(record_dir() / "merge.lock", "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise StoppedError(
                "another merge is already running on this machine; wait for it to finish"
            ) from None
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def run_merge(
    r: Runner, bid: str, *, resume: bool = False, say: Callable[[str], None] = print
) -> Record:
    """`merge <batch id> [--resume]` (req 6, req 7): land the longest green prefix that
    still verifies, PR by PR, and stop before the next merge on any failure. If `gh`
    reports a merge as failed but it actually landed (an ambiguous answer, e.g. a dropped
    connection after GitHub applied the squash), the train still stops rather than guess;
    `--resume` then refuses too (`main` has moved), and the owner reconciles by hand and
    reruns `build`."""
    with _merge_lock():
        return _run_merge_locked(r, bid, resume=resume, say=say)


def _run_merge_locked(r: Runner, bid: str, *, resume: bool, say: Callable[[str], None]) -> Record:
    report = say
    say = lambda msg: report(_redact(msg))  # noqa: E731 - redact before anything is shown
    path = record_path(bid)
    if not path.exists():
        raise StoppedError(f"no record for batch {bid}; run build first")
    record = Record.from_json(path.read_text())
    accepted = record.accepted()
    n = len(accepted)

    if resume:
        if record.merge is None or record.merge.stopped_at_position is None:
            raise StoppedError(f"batch {bid} was not stopped; nothing to resume")
        merged_so_far = list(record.merge.merged)
        # pass-2 review of #640, SHOULD FIX 1: `start` is derived from what actually landed,
        # never trusted from `stopped_at_position` alone, which a stale or buggy save could
        # have left pointing past an unlanded PR.
        start = len(merged_so_far) + 1
        if start != record.merge.stopped_at_position:
            raise StoppedError(
                f"batch {bid}: the recorded stop position does not match what actually "
                "landed; rerun build"
            )
        last_sha = merged_so_far[-1]["main_sha"] if merged_so_far else record.base
        last_tree = merged_so_far[-1]["tree"] if merged_so_far else record.trees[0]
        current = r.main_sha()
        if current != last_sha or r.tree_of_ref(current) != last_tree:
            raise StoppedError(
                f"batch {bid}: origin/main does not match the recorded stop point; rerun build"
            )
        record.merge.resumed_at = r.now().isoformat()
        record.merge.reason = None  # SHOULD FIX 10: the old stop's reason is stale now
    else:
        start, merged_so_far = 1, []
        current = r.main_sha()
        if current != record.base:
            raise StoppedError(
                f"batch {bid}: origin/main ({current[:7]}) is not base {record.base[:7]}; "
                "rerun build"
            )
        record.merge = MergeLog(started_at=r.now().isoformat())
    _save(record)

    # req 7: the frozen record must still match the owner's TESTED comments. Only the
    # accepted PRs are read live (#642, follow-up 2): a dropped/already-merged/ineligible
    # PR is not part of what `merge` lands, so a push to it mid-merge must never abort the
    # whole batch, and its live state (including the fetch-race check in `pr_data`) is
    # never needed here.
    owner_comments: dict[int, list[str]] = {}
    pr_data_by_number: dict[int, PrData] = {}
    for pr_entry in accepted:
        data = r.pr_data(pr_entry.number)
        pr_data_by_number[pr_entry.number] = data
        owner_comments[pr_entry.number] = owner_bodies(data.comments, data.pr.owner)
    if not record_matches_comments(record, owner_comments):
        raise StoppedError(f"batch {bid}'s record no longer matches its TESTED comments")

    # req 6: re-verify every PR from `start` on; PRs already landed stay trivially valid.
    still_valid = [True] * n
    for idx in range(start - 1, n):
        entry = accepted[idx]
        data = pr_data_by_number[entry.number]
        still_valid[idx] = (
            eligibility(data.pr, data.head_checks, data.diff_paths, data.comments) is None
            and data.pr.head == entry.head
        )
    target = mergeable_prefix(record, still_valid)
    green_max = max(record.green_prefixes, default=0)
    # SHOULD FIX 1: a resume whose target shrank below `start` must never post HELD on an
    # already-merged PR, or move `stopped_at_position` backwards.
    held_start = max(target + 1, start)
    broke = False

    def _stop(reason: str, *, extra_sha: str | None = None) -> None:
        """Record a stop (pass-2 review of #640, SHOULD FIX 1): the position to resume from
        is always `len(merged_so_far) + 1` - derived from what actually landed and was
        verified, never from `pos`, so a PR that never landed (or whose landing could not
        be verified) is never skipped on `--resume`. Whenever something is known to have
        merged - including, on the exception path, a best-effort re-read of `main_sha()`
        that moved but was never verified - the reason names that SHA as untested.
        #642, follow-up 10: a second call in the same run (e.g. persisting the first stop
        itself raises, and the exception handler calls `_stop` again) never replaces the
        first reason - it is the one that explains what actually went wrong first; a later
        one is only logged."""
        nonlocal held_start, broke
        held_start, broke = len(merged_so_far) + 1, True
        sha = extra_sha or (merged_so_far[-1]["main_sha"] if merged_so_far else None)
        if sha:
            reason = f"{reason}; {sha} is untested at main's head"
        if record.merge.reason is None:
            record.merge.reason = reason
        else:
            say(f"also: {reason}")
        record.merge.stopped_at_position = held_start
        _save(record)

    for pos in range(start, target + 1):
        entry = accepted[pos - 1]
        previous_tip = merged_so_far[-1]["main_sha"] if merged_so_far else record.base
        expected_tree = record.trees[pos]
        # SHOULD FIX 2/4 (req 6): the live head/eligibility re-read, the merge attempt, the
        # post-merge fetch, the tree and parent checks, and the MERGED post are now ALL one
        # `try`, so anything raising after an earlier PR landed still records the stop
        # position and reason before it propagates - a stopped merge must always leave a
        # trail, never just an exception.
        attempted_merge = False  # #642, follow-up 9: what the exception reason may claim
        try:
            fresh = r.pr_data(entry.number)
            if fresh.pr.head != entry.head or (
                eligibility(fresh.pr, fresh.head_checks, fresh.diff_paths, fresh.comments)
                is not None
            ):
                _stop(f"#{entry.number}'s head or eligibility changed since build")
                break

            attempted_merge = True
            attempt = _merge_with_retries(r, entry.number, entry.head, say)
            new_main = r.main_sha()
            if attempt.outcome != "merged" and new_main == previous_tip:
                _stop(f"#{entry.number} could not be merged: {attempt.detail}")
                break
            if attempt.outcome != "merged":
                # `gh` reported failure, but `main` moved anyway: an ambiguous answer (e.g. a
                # dropped connection after GitHub applied the squash). Treat the landing as
                # real and keep verifying it below, rather than guessing it didn't happen.
                say(
                    f"#{entry.number}: gh reported {attempt.outcome!r}, "
                    f"but main moved to {new_main}"
                )
            landed_tree = r.tree_of_ref(new_main)
            parent_ok = r.parent_of(new_main) == previous_tip

            merged_so_far.append(
                {
                    "number": entry.number,
                    "main_sha": new_main,
                    "tree": expected_tree,  # SHOULD FIX 9: the expected tree, kept distinct
                    "landed_tree": landed_tree,  # ... from what actually landed
                }
            )
            record.merge.merged = list(merged_so_far)
            _save(record)  # record the landing before posting anything about it (SHOULD FIX 2)
            if not parent_ok or landed_tree != expected_tree:
                # #642, follow-up 7: an unconfirmed landing (parent or tree mismatch, e.g. a
                # concurrent hand merge) must never be reported to the PR as `MERGED` - the
                # comment is posted only once the landing is confirmed, below.
                check = "parent" if not parent_ok else "tree"  # SHOULD FIX 11: name which
                _stop(f"the landed {check} for #{entry.number} does not match tree_{pos}")
                break
            _post(r, say, entry.number, comment("MERGED", bid, main_sha=new_main))
        except Exception:
            # pass-2 review, SHOULD FIX 1: re-read `main_sha()` one more time, best-effort,
            # to name the freshest known head even if the earlier read inside the `try` is
            # what raised. Never recorded in `merged_so_far` itself: its landing (and tree)
            # were never verified, so `--resume` must still retry PR #{entry.number}, not
            # skip past it.
            current_sha = None
            with contextlib.suppress(Exception):
                current_sha = r.main_sha()
            extra = current_sha if current_sha != previous_tip else None
            # #642, follow-up 9: the reason must say what actually happened - `pr_data` or
            # the eligibility re-read can raise before any merge was even attempted for this
            # PR, and that must never be reported as an unverified merge.
            when = "after gh pr merge" if attempted_merge else "before attempting to merge it"
            _stop(
                f"could not verify #{entry.number}'s landing {when}; "
                "reconcile main by hand and rerun build",
                extra_sha=extra,
            )
            raise

    # SHOULD FIX 3/5: "stopped" means either an in-loop failure (`broke`, however far along -
    # even on the very last PR), or there is still PR within a recorded green prefix left
    # un-merged; a red batch that lands its whole available green prefix is simply complete.
    held_upper = min(n, green_max)
    stopped = broke or held_start <= held_upper
    record.merge.stopped_at_position = held_start if stopped else None
    _save(record)
    held_reason = record.merge.reason or "not re-verified"
    for entry in accepted[held_start - 1 : held_upper]:
        _post(r, say, entry.number, comment("HELD", bid, reason=held_reason))
    _save(record)
    say(
        f"batch {bid}: merge complete"
        if record.merge.stopped_at_position is None
        else f"STOPPED: {record.merge.reason or 'merge stopped'}"
    )

    for branch in (record.train_branch, *(p.branch for p in record.probes)):
        # #642, follow-up 1: a record hand-edited to name a branch outside `train/*` makes
        # `delete_branch` refuse it (same rule as `push`); the merges above already
        # succeeded, so that refusal is reported and the loop moves on, never turning a
        # completed merge into an exit code of 1.
        try:
            deleted = r.delete_branch(branch)
        except StoppedError as exc:
            say(f"could not delete branch {branch}: {exc}")
            continue
        if not deleted:
            say(f"could not delete branch {branch}; remove it by hand")
    return record


def run_status(bid: str | None, say: Callable[[str], None] = print) -> None:
    """`status [<batch id>]`: print one record, or list every record on this machine."""
    if bid:
        path = record_path(bid)
        if not path.exists():
            raise StoppedError(f"no record for batch {bid}")
        say(path.read_text())
        return
    rdir = record_dir()
    if not rdir.exists():
        say("no batches recorded")
        return
    for path in sorted(rdir.glob("*.json")):
        record = Record.from_json(path.read_text())
        say(f"{record.batch_id}: {record.outcome} ({len(record.accepted())} accepted)")


def _build_finished(record: Record) -> bool:
    """Whether `build` is done with this record (pass-1 review of #640, SHOULD FIX 8): a
    `green` run is always done; a `red` one is done once its bisect actually concluded
    (a culprit found, or `held` populated by an inconclusive-probe stop); `inconclusive`
    and `None` (still running, or crashed before an outcome was even set) are not -
    `build --resume` or a human may still need the branch, and a live build's worktree."""
    if record.outcome == "green":
        return True
    if record.outcome == "red":
        return record.culprit is not None or bool(record.held)
    return False


def run_prune(r: Runner, say: Callable[[str], None] = print) -> None:
    """`prune`: remove leftover worktrees and delete `train/*` branches of finished batches.
    A build still in flight (`outcome` `None` or `inconclusive`, or a `red` run whose bisect
    hasn't concluded) is left alone - `build --resume` or another window may still need its
    worktree and branches. A batch already pruned is not retried. Takes the same lock
    `merge` uses (#642, follow-up 8): saving `record.pruned` must never interleave with a
    concurrent `merge` reading or writing that same record."""
    with _merge_lock():
        _run_prune_locked(r, say)


def _run_prune_locked(r: Runner, say: Callable[[str], None]) -> None:
    report = say
    say = lambda msg: report(_redact(msg))  # noqa: E731 - redact before anything is shown
    rdir = record_dir()
    if not rdir.exists():
        say("nothing to prune")
        return
    records: dict[str, Record] = {}
    for path in sorted(rdir.glob("*.json")):
        record = Record.from_json(path.read_text())
        records[record.batch_id] = record
    for wpath in sorted(rdir.glob("worktree-*")):
        bid = wpath.name.removeprefix("worktree-")
        record = records.get(bid)
        # #642, follow-up 6: `build` creates the worktree before it can save the batch's
        # first record (it has nothing to save until the PRs are known), so a worktree with
        # no record yet may belong to a build still in flight. Fail closed: skip it, exactly
        # as an unfinished build's own record would be skipped, rather than guess it is
        # orphaned.
        if record is None or not _build_finished(record):
            continue  # a live build (or one that crashed before finishing) owns this
        say(f"removing leftover worktree {wpath}")
        r.remove_worktree(wpath)
    for record in records.values():
        if record.pruned or not _build_finished(record):
            continue
        ok = True
        for branch in (record.train_branch, *(p.branch for p in record.probes)):
            if not r.delete_branch(branch):
                ok = False
                say(f"could not delete branch {branch}; remove it by hand")
        record.pruned = ok
        _save(record)


# -- the real runner ------------------------------------------------------------------------


class ShellRunner:
    """Real git, gh and the filesystem. Thin: every rule lives in the functions above."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._repo: str | None = None

    def _git(self, cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(cwd), *args], check=check, capture_output=True, text=True
        )

    def _git_out(self, cwd: Path, *args: str) -> str:
        try:
            return self._git(cwd, *args, check=True).stdout.strip()
        except subprocess.CalledProcessError as exc:
            raise StoppedError(f"git {' '.join(args[:2])} failed: {_redact(exc.stderr)}") from None

    def _gh(self, *args: str) -> str:
        try:
            return subprocess.run(
                ["gh", *args], cwd=self.root, check=True, capture_output=True, text=True
            ).stdout
        except FileNotFoundError:
            raise StoppedError("gh CLI not found; install it and run `gh auth login`") from None
        except subprocess.CalledProcessError as exc:
            raise StoppedError(f"gh {' '.join(args[:2])} failed: {_redact(exc.stderr)}") from None

    def _repo_name(self) -> str:
        if self._repo is None:
            raw = json.loads(self._gh("repo", "view", "--json", "nameWithOwner"))
            self._repo = str(raw["nameWithOwner"])
        return self._repo

    def main_sha(self) -> str:
        self._git_out(self.root, "fetch", "-q", "origin", "main")
        return self._git_out(self.root, "rev-parse", "origin/main")

    def open_pr_numbers(self) -> list[int]:
        raw = json.loads(self._gh("pr", "list", "--state", "open", "--json", "number", "-L", "200"))
        return [int(p["number"]) for p in raw]

    def _head_checks(self, number: int) -> ready_pr.HeadChecks:
        raw = json.loads(
            self._gh("pr", "view", str(number), "--json", "headRefOid,statusCheckRollup")
        )
        runs = tuple(
            ready_pr.CheckRun(
                str(c.get("name") or c.get("context") or "?"),
                str(c.get("status") or ("COMPLETED" if c.get("conclusion") else "")),
                str(c.get("conclusion") or c.get("state") or ""),
            )
            for c in raw.get("statusCheckRollup") or []
        )
        return ready_pr.HeadChecks(str(raw["headRefOid"]), runs)

    def pr_data(self, number: int) -> PrData:
        raw = json.loads(
            self._gh(
                "pr",
                "view",
                str(number),
                "--json",
                "number,author,isCrossRepository,baseRefName,state,isDraft,headRefName,"
                "headRefOid,body,labels,comments,title",
            )
        )
        repo = self._repo_name()
        pr = PullRequest(
            number=int(raw["number"]),
            author=str(raw["author"]["login"]),
            head_repo=repo if not raw.get("isCrossRepository") else f"fork/{repo}",
            repo=repo,
            state=str(raw["state"]),
            is_draft=bool(raw["isDraft"]),
            base=str(raw["baseRefName"]),
            labels=tuple(str(label_obj["name"]) for label_obj in raw.get("labels", [])),
            head=str(raw["headRefOid"]),
            branch=str(raw["headRefName"]),
            body=str(raw.get("body") or ""),
            title=str(raw.get("title") or ""),
        )
        comments = tuple(
            Comment(author=str(c.get("author", {}).get("login", "")), body=str(c.get("body", "")))
            for c in raw.get("comments", [])
        )
        if pr.head_repo != pr.repo or pr.author != pr.owner:
            # req 1 check (0): a fork or another author is ineligible regardless of its
            # diff or checks, and its head may not even be an object we can fetch. Stop
            # here rather than let a crafted fork PR fail this call and block the whole
            # default build (SHOULD FIX, safety review of #521).
            return PrData(pr, comments, ready_pr.HeadChecks(pr.head, ()), ())
        # the owner's own head may never have been fetched into this clone (a push from
        # another clone, or a GitHub "Update branch"); `refs/pull/<n>/head` always exists.
        # #642, follow-up 5: fetched into a private ref, never the shared `FETCH_HEAD` - a
        # concurrent `merge_train` run in this same clone (e.g. `merge` fetching a different
        # PR) would otherwise overwrite it between this fetch and the read below.
        private_ref = f"refs/merge-train/pr-{number}"
        refspec = f"+refs/pull/{number}/head:{private_ref}"
        self._git(self.root, "fetch", "-q", "origin", refspec, check=False)
        # #524, follow-up 3: a push to the PR between reading its metadata and this fetch
        # would otherwise compute the diff against a stale head; fail closed instead.
        fetched = self._git(self.root, "rev-parse", private_ref, check=False)
        fetched_sha = fetched.stdout.strip() if fetched.returncode == 0 else ""
        if fetched_sha and fetched_sha != pr.head:
            raise StoppedError(
                f"PR #{number}'s head changed between reading it ({pr.head[:7]}) and "
                f"fetching it ({fetched_sha[:7]}); refusing a diff against a stale head"
            )
        diff = self._git_out(
            self.root,
            "-c",
            "core.quotePath=false",
            "diff",
            "--no-renames",
            "--name-only",
            f"origin/main...{pr.head}",
        )
        return PrData(pr, comments, self._head_checks(number), tuple(diff.splitlines()))

    def add_worktree(self, path: Path, base: str) -> None:
        self._git_out(self.root, "worktree", "add", "--detach", str(path), base)

    def remove_worktree(self, path: Path) -> None:
        result = subprocess.run(
            ["git", "-C", str(self.root), "worktree", "remove", "--force", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            msg = f"could not remove worktree {path}; run `prune` by hand: {_redact(result.stderr)}"
            print(f"STOPPED: {msg}")

    def merge_squash(self, worktree: Path, head: str) -> bool:
        return self._git(worktree, "merge", "--squash", head, check=False).returncode == 0

    def commit_squash(self, worktree: Path, message: str) -> str:
        self._git_out(worktree, "commit", "-m", message)
        return self._git_out(worktree, "rev-parse", "HEAD")

    def abort_merge(self, worktree: Path) -> None:
        if self._git(worktree, "merge", "--abort", check=False).returncode != 0:
            self._git(worktree, "reset", "--hard", "HEAD", check=False)

    def conflicted_paths(self, worktree: Path) -> list[str]:
        return self._git_out(worktree, "diff", "--name-only", "--diff-filter=U").splitlines()

    def is_ancestor(self, maybe_ancestor: str, ref: str) -> bool:
        return (
            self._git(
                self.root, "merge-base", "--is-ancestor", maybe_ancestor, ref, check=False
            ).returncode
            == 0
        )

    def head_sha(self, worktree: Path) -> str:
        return self._git_out(worktree, "rev-parse", "HEAD")

    def tree_of(self, worktree: Path) -> str:
        return self._git_out(worktree, "rev-parse", "HEAD^{tree}")

    def push(self, worktree: Path, ref: str, branch: str) -> None:
        if not branch.startswith("train/"):
            raise StoppedError(f"refusing to push a non-train/ branch: {branch}")
        self._git_out(worktree, "push", "origin", f"{ref}:refs/heads/{branch}")

    def delete_branch(self, branch: str) -> bool:
        if not branch.startswith("train/"):
            raise StoppedError(f"refusing to delete a non-train/ branch: {branch}")
        result = self._git(self.root, "push", "origin", "--delete", branch, check=False)
        return result.returncode == 0

    def tree_of_ref(self, ref: str) -> str:
        return self._git_out(self.root, "rev-parse", f"{ref}^{{tree}}")

    def parent_of(self, sha: str) -> str:
        result = self._git(self.root, "rev-parse", f"{sha}^", check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    def merge_pr(self, number: int, head: str) -> MergeAttempt:
        # #642, follow-up 3: a hung `gh pr merge` must never hang `merge` itself, and
        # whether it actually landed is then unknown - an ambiguous, fail-closed outcome
        # (never `merged`), so the caller's own post-attempt `main_sha()` check decides.
        try:
            result = subprocess.run(
                ["gh", "pr", "merge", str(number), "--squash", "--match-head-commit", head],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=MERGE_PR_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return MergeAttempt(
                "failed", f"gh pr merge timed out after {MERGE_PR_TIMEOUT_S}s; outcome unknown"
            )
        if result.returncode == 0:
            return MergeAttempt("merged")
        text = _redact(((result.stderr or "") + (result.stdout or "")).strip())
        low = text.lower()
        # #642, follow-up 4: a permanent refusal (a real conflict, a branch-protection or
        # review-policy block) must stop at once, never retry for ~2 minutes as if it were
        # the transient "GitHub hasn't finished computing mergeability yet" case. Checked
        # first, since GitHub's own wording for a real conflict still says "not mergeable".
        permanent_phrases = (
            "cannot be cleanly created",  # a real conflict
            "required status check",
            "review is required",
            "changes requested",
            "protected branch",
            "approving review",
        )
        if any(phrase in low for phrase in permanent_phrases):
            return MergeAttempt("failed", text)
        retryable_phrases = ("mergeable state is unknown", "try again", "is in unstable status")
        retryable = any(phrase in low for phrase in retryable_phrases)
        return MergeAttempt("retryable" if retryable else "failed", text)

    def find_run(self, sha: str, branch: str) -> RunInfo:
        # Filtered by workflow file too (#524, follow-up 2): safe today since ci.yml is the
        # only workflow, but this keeps a second one from ever being matched here by mistake.
        raw = json.loads(
            self._gh(
                "run",
                "list",
                "--branch",
                branch,
                "--event",
                "push",
                "--workflow",
                "ci.yml",
                "--json",
                "databaseId,headSha,status,conclusion,url,attempt",
                "-L",
                "20",
            )
        )
        matches = [run for run in raw if run.get("headSha") == sha]
        if not matches:
            return RunInfo(None, 0, None, None, None)
        latest = matches[0]
        run_id = int(latest["databaseId"])
        jobs_raw = json.loads(self._gh("run", "view", str(run_id), "--json", "jobs"))
        steps: list[Step] = []
        duration = 0.0
        for job in jobs_raw.get("jobs", []):
            for s in job.get("steps", []):
                steps.append(Step(str(s.get("name", "")), str(s.get("conclusion") or "")))
            started, completed = job.get("startedAt"), job.get("completedAt")
            if started and completed:
                duration = max(
                    duration,
                    (
                        datetime.fromisoformat(completed) - datetime.fromisoformat(started)
                    ).total_seconds(),
                )
        conclusion = latest.get("conclusion") or None
        return RunInfo(
            run_id,
            int(latest.get("attempt", 1)),
            str(latest.get("status")) if latest.get("status") else None,
            str(conclusion) if conclusion else None,
            str(latest.get("url") or ""),
            tuple(steps),
            duration,
        )

    def rerun(self, run_id: int) -> None:
        self._gh("run", "rerun", str(run_id), "--failed")

    def post_comment(self, number: int, text: str) -> None:
        self._gh("pr", "comment", str(number), "--body", _redact(text))

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self) -> datetime:
        return datetime.now(UTC)


# -- entry point ------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="merge_train.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build and test a batch of ready PRs together")
    build.add_argument("prs", type=int, nargs="*", help="PR numbers; default: every eligible PR")
    build.add_argument(
        "--order", type=int, nargs="+", help="explicit order (the orchestrator reorders)"
    )
    build.add_argument("--timeout-min", type=int, default=DEFAULT_TIMEOUT_S // 60)
    build.add_argument("--poll-s", type=int, default=DEFAULT_POLL_S)
    build.add_argument("--resume", metavar="BATCH_ID", help="re-attach to a batch's branch/run")
    merge = sub.add_parser("merge", help="land the longest green prefix that still verifies")
    merge.add_argument("batch_id")
    merge.add_argument("--resume", action="store_true", help="continue a stopped merge")
    status = sub.add_parser("status", help="print a record, or list every record")
    status.add_argument("batch_id", nargs="?")
    sub.add_parser("prune", help="delete train/* branches of finished batches")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "build" and args.resume and (args.prs or args.order):
        parser.error("--resume takes no PR list or --order")
    root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], check=True, capture_output=True, text=True
        ).stdout.strip()
    )
    r = ShellRunner(root)
    try:
        if args.command == "build":
            timeout_s = args.timeout_min * 60
            if args.resume:
                record = run_build_resume(r, args.resume, timeout_s=timeout_s, poll_s=args.poll_s)
            else:
                record = run_build(
                    r, args.prs or None, args.order, timeout_s=timeout_s, poll_s=args.poll_s
                )
            print(record.to_json())
            if record.outcome == "green":
                return 0
            detail = record.detail or (f"culprit #{record.culprit}" if record.culprit else "")
            print(f"STOPPED: {record.outcome}{f' ({detail})' if detail else ''}")
            return 1
        if args.command == "merge":
            record = run_merge(r, args.batch_id, resume=args.resume)
            print(record.to_json())
            if record.merge is not None and record.merge.stopped_at_position is not None:
                print(f"STOPPED: {record.merge.reason or 'merge stopped'}")
                return 1
            return 0
        if args.command == "status":
            run_status(args.batch_id)
            return 0
        if args.command == "prune":
            run_prune(r)
            return 0
    except StoppedError as exc:
        print(f"STOPPED: {exc}")
        return 1
    parser.error(f"unknown command {args.command!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

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

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

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
        lines.append(f"head {detail.pop('head')}")  # read back exactly by check (d)
    if outcome == "INCONCLUSIVE":
        reason = str(detail.pop("reason"))
        uv_lock = [int(n) for n in detail.pop("uv_lock_prs", ())]
        lines += [
            reason,
            "Check GitHub Actions, then rerun: "
            f"uv run python scripts/merge_train.py build --resume {batch}",
        ]
        if reason.startswith("Install") and len(uv_lock) >= 2:
            names = ", ".join(f"#{n}" for n in uv_lock)
            lines.append(
                f"{names} all touch uv.lock, which --resume cannot clear: "
                "drop one of them from the next build"
            )
    lines += [f"{key}: {value}" for key, value in detail.items()]
    return "\n".join(lines)

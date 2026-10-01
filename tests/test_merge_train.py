"""Merge train, pure core (plan T72; spec docs/specs/merge-train.md reqs 1, 2, 4 to 8).

No network, no git: every function under test is pure. AC numbers are the spec's.
"""

from __future__ import annotations

import importlib.util
import re
import sys
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
            (f"merge-train: CULPRIT batch 20261001-120000-abcdef1\nhead {HEAD}",),
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
    culprit = _c(f"merge-train: CULPRIT batch 20261001-120000-abcdef1\nhead {HEAD}")
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
    detail: dict[str, object] = (
        {"position": 1, "head": HEAD, "prefixes": {1: "u"}}
        if outcome == "TESTED"
        else {"reason": "r"}
    )
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

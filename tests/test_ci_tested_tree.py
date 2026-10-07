"""Main's shard skip (#1192): skip only an exact, fully tested PR-head tree; else run."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ci_tested_tree.py"
_spec = importlib.util.spec_from_file_location("ci_tested_tree", _PATH)
assert _spec and _spec.loader
ci_tested_tree = importlib.util.module_from_spec(_spec)
sys.modules["ci_tested_tree"] = ci_tested_tree
_spec.loader.exec_module(ci_tested_tree)

REPO = "o/r"
SHA = "m" * 40
PARENT = "p" * 40
HEAD = "h" * 40
TREE = "t" * 40
SHARDS = 8


def _run(name: str, suite: int = 1, conclusion: str = "success", **kw: Any) -> dict[str, Any]:
    run: dict[str, Any] = {
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
        "app": {"slug": "github-actions"},
        "check_suite": {"id": suite},
    }
    run.update(kw)
    return run


def _full_suite(suite: int = 1) -> list[dict[str, Any]]:
    return [
        _run("checks-fast", suite),
        *(_run(f"pytest-shard ({i})", suite) for i in range(SHARDS)),
        _run("checks", suite),
    ]


class FakeApi:
    def __init__(self, **over: Any) -> None:
        runs = over.pop("runs", _full_suite())
        self.answers: dict[str, Any] = {
            f"repos/{REPO}/commits/{SHA}/pulls": [
                {
                    "number": 70,
                    "merge_commit_sha": SHA,
                    "merged_at": "2026-10-07T12:00:00Z",
                    "base": {"ref": "main"},
                    "head": {"sha": HEAD},
                }
            ],
            f"repos/{REPO}/git/commits/{SHA}": {
                "tree": {"sha": TREE},
                "parents": [{"sha": PARENT}],
            },
            f"repos/{REPO}/git/commits/{HEAD}": {"tree": {"sha": TREE}, "parents": []},
            f"repos/{REPO}/compare/{PARENT}...{HEAD}": {"status": "ahead"},
            f"repos/{REPO}/commits/{HEAD}/check-runs?filter=latest&per_page=100": {
                "total_count": len(runs),
                "check_runs": runs,
            },
        }
        self.answers.update(over)

    def get(self, path: str) -> Any:
        answer = self.answers[path]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _decide(api: FakeApi, shards: int = SHARDS) -> bool:
    skip, reason = ci_tested_tree.decide(api, REPO, SHA, shards)
    assert reason
    return bool(skip)


def test_a_squash_of_a_fully_tested_up_to_date_head_skips() -> None:
    assert _decide(FakeApi())


def test_identical_compare_status_still_counts_as_contained() -> None:
    assert _decide(FakeApi(**{f"repos/{REPO}/compare/{PARENT}...{HEAD}": {"status": "identical"}}))


@pytest.mark.parametrize(
    "pulls",
    [
        [],
        [{"number": 70, "merge_commit_sha": "x" * 40, "merged_at": "t", "base": {"ref": "main"}}],
        [{"number": 70, "merge_commit_sha": SHA, "merged_at": None, "base": {"ref": "main"}}],
        [{"number": 70, "merge_commit_sha": SHA, "merged_at": "t", "base": {"ref": "train/x"}}],
        [
            {"number": n, "merge_commit_sha": SHA, "merged_at": "t", "base": {"ref": "main"}}
            for n in (70, 71)
        ],
        [{"number": 70, "merge_commit_sha": SHA, "merged_at": "t", "base": {"ref": "main"}}],
    ],
    ids=["no-pr", "other-merge-sha", "unmerged", "other-base", "two-prs", "no-head"],
)
def test_no_single_merged_pr_with_a_head_runs(pulls: list[dict[str, Any]]) -> None:
    assert not _decide(FakeApi(**{f"repos/{REPO}/commits/{SHA}/pulls": pulls}))


def test_a_merge_commit_or_root_commit_runs() -> None:
    two = {"tree": {"sha": TREE}, "parents": [{"sha": PARENT}, {"sha": HEAD}]}
    assert not _decide(FakeApi(**{f"repos/{REPO}/git/commits/{SHA}": two}))
    root = {"tree": {"sha": TREE}, "parents": []}
    assert not _decide(FakeApi(**{f"repos/{REPO}/git/commits/{SHA}": root}))


def test_a_tree_that_differs_from_the_head_runs() -> None:
    other = {"tree": {"sha": "u" * 40}, "parents": []}
    assert not _decide(FakeApi(**{f"repos/{REPO}/git/commits/{HEAD}": other}))
    empty = {"tree": {}, "parents": [{"sha": PARENT}]}
    assert not _decide(FakeApi(**{f"repos/{REPO}/git/commits/{SHA}": empty}))


@pytest.mark.parametrize("status", ["behind", "diverged", None])
def test_a_head_that_lacks_mains_parent_runs(status: str | None) -> None:
    assert not _decide(FakeApi(**{f"repos/{REPO}/compare/{PARENT}...{HEAD}": {"status": status}}))


def _runs_without(name: str) -> list[dict[str, Any]]:
    return [r for r in _full_suite() if r["name"] != name]


@pytest.mark.parametrize(
    "runs",
    [
        [],
        _runs_without("checks"),
        _runs_without("pytest-shard (7)"),
        # a docs-only run: shards skipped, checks green; the tree was never tested
        [
            _run("checks-fast"),
            _run("pytest-shard", conclusion="skipped"),
            _run("checks"),
        ],
        [*_runs_without("pytest-shard (3)"), _run("pytest-shard (3)", conclusion="skipped")],
        [*_runs_without("pytest-shard (3)"), _run("pytest-shard (3)", conclusion="failure")],
        [*_runs_without("checks"), _run("checks", status="in_progress", conclusion=None)],
        # a failed attempt next to a passing one in the same suite is not a clean pass
        [*_full_suite(), _run("checks", conclusion="failure")],
        # shards from one run and `checks` from another never add up to a full run
        [
            *[r for r in _full_suite(1) if r["name"] != "checks"],
            _run("checks", suite=2),
        ],
        # the draft run's aggregator is not the gate
        [*_runs_without("checks"), _run("checks (draft, no shards)")],
        # a `checks` from some other app is not CI's
        [*_runs_without("checks"), _run("checks", app={"slug": "someone-else"})],
        [*_runs_without("checks"), _run("checks", check_suite=None)],
    ],
    ids=[
        "none",
        "no-checks",
        "missing-shard",
        "docs-only",
        "skipped-shard",
        "failed-shard",
        "checks-running",
        "mixed-attempts",
        "split-suites",
        "draft-aggregator",
        "other-app",
        "no-suite",
    ],
)
def test_no_complete_green_full_run_on_the_head_runs(runs: list[dict[str, Any]]) -> None:
    assert not _decide(FakeApi(runs=runs))


def test_any_one_full_green_suite_is_enough() -> None:
    draft = [_run("checks-fast", 5), _run("checks (draft, no shards)", 5)]
    assert _decide(FakeApi(runs=[*draft, *_full_suite(9)]))


def test_a_larger_shard_count_than_the_run_had_runs() -> None:
    assert not _decide(FakeApi(), shards=SHARDS + 1)
    assert not _decide(FakeApi(), shards=0)


def test_more_check_runs_than_one_page_runs() -> None:
    path = f"repos/{REPO}/commits/{HEAD}/check-runs?filter=latest&per_page=100"
    api = FakeApi()
    api.answers[path] = {**api.answers[path], "total_count": 101}
    assert not _decide(api)


def test_main_prints_skip_only_when_decided(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("PYTEST_SHARD_COUNT", str(SHARDS))
    assert ci_tested_tree.main([SHA], api=FakeApi()) == 0
    out = capsys.readouterr()
    assert out.out == "skip\n"
    assert "PR #70" in out.err


@pytest.mark.parametrize(
    ("argv", "env", "api"),
    [
        ([], {"GITHUB_REPOSITORY": REPO, "PYTEST_SHARD_COUNT": "8"}, FakeApi()),
        ([SHA], {"PYTEST_SHARD_COUNT": "8"}, FakeApi()),
        ([SHA], {"GITHUB_REPOSITORY": REPO}, FakeApi()),
        ([SHA], {"GITHUB_REPOSITORY": REPO, "PYTEST_SHARD_COUNT": "eight"}, FakeApi()),
        (
            [SHA],
            {"GITHUB_REPOSITORY": REPO, "PYTEST_SHARD_COUNT": "8"},
            FakeApi(**{f"repos/{REPO}/git/commits/{SHA}": RuntimeError("HTTP 502")}),
        ),
        (
            [SHA],
            {"GITHUB_REPOSITORY": REPO, "PYTEST_SHARD_COUNT": "8"},
            FakeApi(**{f"repos/{REPO}/commits/{SHA}/pulls": {"message": "Not Found"}}),
        ),
    ],
    ids=["no-sha", "no-repo", "no-count", "bad-count", "api-error", "odd-answer"],
)
def test_main_fails_closed_to_run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    env: dict[str, str],
    api: FakeApi,
) -> None:
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("PYTEST_SHARD_COUNT", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert ci_tested_tree.main(argv, api=api) == 0
    out = capsys.readouterr()
    assert out.out == "run\n"
    assert "run" in out.err

"""Tests for scripts/fragments.py: one fragment per PR, folded into STATUS "Recently done"
(the last N kept) and CHANGELOG [Unreleased] (#351), with the old two-directory layout
still read during the transition."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "fragments", Path(__file__).resolve().parents[1] / "scripts" / "fragments.py"
)
assert _SPEC is not None and _SPEC.loader is not None
fragments = importlib.util.module_from_spec(_SPEC)
sys.modules["fragments"] = fragments
_SPEC.loader.exec_module(fragments)

STATUS = """# Status

**Updated:** 2026-09-24

## Recently done
- one (PR #1)
- two (PR #2)

## Teams
text

## In progress
- something
"""

CHANGELOG = """# Changelog

## [Unreleased]
### Added
- a (#1)

### Changed
- b (#2)

## [0.1.0] - 2026-09-24
### Added
- old
"""


def _frag(name: str, text: str) -> fragments.Fragment:
    return fragments.parse_fragment(Path(name), text)


def _legacy(name: str, text: str) -> fragments.Fragment:
    return fragments.parse_legacy_status_fragment(Path(name), text)


# parsing


def test_one_fragment_holds_the_status_line_and_changelog_bullets() -> None:
    f = _frag("70-ready.md", "- #70 thing (PR #71)\n### Added\n- a\n\n### Changed\n- c\n")
    assert f.issue == 70
    assert f.status == ["- #70 thing (PR #71)"]
    assert f.sections == {"### Added": ["- a"], "### Changed": ["- c"]}
    # Either half alone is a fragment.
    assert _frag("70-a.md", "- #70 docs only\n").sections == {}
    assert _frag("70-b.md", "### Fixed\n- f\n").status == []


def test_fragment_shape_errors() -> None:
    with pytest.raises(fragments.FragmentError, match="one STATUS line"):
        _frag("70-x.md", "- one\n- two\n")
    with pytest.raises(fragments.FragmentError, match="240"):
        _frag("70-x.md", "- " + "x" * 300 + "\n")
    with pytest.raises(fragments.FragmentError, match="bullet"):
        _frag("70-x.md", "just prose\n")
    with pytest.raises(fragments.FragmentError, match="unknown heading"):
        _frag("70-x.md", "### Misc\n- a\n")
    with pytest.raises(fragments.FragmentError, match="no bullets"):
        _frag("70-x.md", "### Added\n")
    with pytest.raises(fragments.FragmentError, match="empty"):
        _frag("70-x.md", "\n\n")
    with pytest.raises(fragments.FragmentError, match="name"):
        _frag("ready.md", "- x\n")
    with pytest.raises(fragments.FragmentError, match="name"):
        _frag("70-Ready.md", "- x\n")


def test_legacy_status_fragment_is_bullets_only() -> None:
    f = _legacy("70-ready.md", "- #70 thing (PR #71)\n- second\n")
    assert f.status == ["- #70 thing (PR #71)", "- second"]
    with pytest.raises(fragments.FragmentError, match="bullet"):
        _legacy("70-ready.md", "### Added\n- a\n")


def test_recent_line_links_and_cuts() -> None:
    assert fragments.recent_line("- did it (PR #9)", 70) == "- did it (PR #9)"
    assert fragments.recent_line("- did it", 70) == "- did it (#70)"
    long = "- " + " ".join(["word"] * 100) + " (#70)"
    cut = fragments.recent_line(long, 70)
    assert len(cut) <= fragments.STATUS_LINE_MAX
    assert cut.endswith("… (#70)")
    assert "wor…" not in cut  # cut at a word boundary


# folding


def test_fold_status_appends_in_issue_order_and_keeps_the_last_n() -> None:
    out = fragments.fold_status(
        STATUS, [_frag("80-b.md", "- eighty\n"), _legacy("70-a.md", "- seventy (#70)\n- bis\n")]
    )
    assert (
        "## Recently done\n- one (PR #1)\n- two (PR #2)\n- seventy (#70)\n- bis (#70)\n"
        "- eighty (#80)\n\n## Teams" in out
    )
    trimmed = fragments.fold_status(STATUS, [_frag("80-b.md", "- eighty\n")], keep=2)
    assert "## Recently done\n- two (PR #2)\n- eighty (#80)\n\n## Teams" in trimmed
    assert trimmed.count("- something") == 1  # other sections untouched


def test_fold_status_trims_to_ten_by_default() -> None:
    bullets = "\n".join(f"- old {i} (#{i})" for i in range(1, 16))
    text = f"# S\n\n## Recently done\nA note line.\n{bullets}\n\n## Teams\nx\n"
    out = fragments.fold_status(text, [_frag("99-z.md", "- newest (#99)\n")])
    section = out.split("## Recently done\n")[1].split("\n\n## Teams")[0].splitlines()
    assert section[0] == "A note line."
    assert section[1:] == [f"- old {i} (#{i})" for i in range(7, 16)] + ["- newest (#99)"]
    assert len(section[1:]) == fragments.STATUS_RECENT_N


def test_fold_status_with_nothing_new_still_trims() -> None:
    bullets = "\n".join(f"- d{i} (#{i})" for i in range(12))
    out = fragments.fold_status(f"## Recently done\n{bullets}\n\n## Teams\n", [])
    assert out.count("- d") == fragments.STATUS_RECENT_N


def test_fold_status_into_an_empty_section() -> None:
    out = fragments.fold_status("## Recently done\n\n## Teams\n", [_frag("5-a.md", "- a (#5)\n")])
    assert out == "## Recently done\n- a (#5)\n\n## Teams\n"


def test_fold_keeps_a_wrapped_bullet_together() -> None:
    text = "## Recently done\n- one\n  continued\n\n## Teams\n"
    out = fragments.fold_status(text, [_frag("70-a.md", "- new (#70)\n")])
    assert out == "## Recently done\n- one\n  continued\n- new (#70)\n\n## Teams\n"


def test_fold_changelog_appends_under_existing_and_new_headings() -> None:
    frags = [
        _frag("80-b.md", "### Added\n- add80\n### Fixed\n- fix80\n"),
        _frag(
            "70-a.md", "- status only goes to STATUS\n### Added\n- add70\n### Changed\n- chg70\n"
        ),
    ]
    out = fragments.fold_changelog(CHANGELOG, frags)
    unreleased = out.split("## [0.1.0]")[0]
    assert "### Added\n- a (#1)\n- add70\n- add80\n" in unreleased
    assert "### Changed\n- b (#2)\n- chg70\n" in unreleased
    assert "### Fixed\n- fix80\n" in unreleased
    assert "status only" not in out
    assert out.endswith("### Added\n- old\n")  # released section untouched
    assert unreleased.index("### Fixed") > unreleased.index("### Changed")


def test_fold_changelog_creates_unreleased_headings_when_empty() -> None:
    text = "# Changelog\n\n## [Unreleased]\n\n## [0.1.0] - x\n### Added\n- old\n"
    out = fragments.fold_changelog(text, [_frag("70-a.md", "### Added\n- new\n")])
    assert "## [Unreleased]\n### Added\n- new\n\n## [0.1.0] - x\n" in out
    assert fragments.fold_changelog(text, [_frag("70-b.md", "- status\n")]) == text


# commands


def _repo(tmp_path: Path) -> list[str]:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "STATUS.md").write_text(STATUS)
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG)
    (tmp_path / "changelog.d").mkdir()
    (tmp_path / "changelog.d" / "README.md").write_text("ignored\n")
    return ["--root", str(tmp_path)]


def test_add_writes_one_file_then_check_show_fold(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo(tmp_path)
    assert fragments.main([*root, "check"]) == 0
    args = ["add", "70", "--slug", "ready", "--status", "#70 done (PR #71)", "--added", "x (#70)"]
    assert fragments.main([*root, *args]) == 0
    assert (tmp_path / "changelog.d" / "70-ready.md").read_text() == (
        "- #70 done (PR #71)\n### Added\n- x (#70)\n"
    )
    assert not (tmp_path / "docs" / "status.d").exists()
    with pytest.raises(SystemExit, match="exists"):
        fragments.main([*root, "add", "70", "--slug", "ready", "--status", "again"])

    capsys.readouterr()
    assert fragments.main([*root, "show"]) == 0
    shown = capsys.readouterr().out
    assert "## Recently done (pending" in shown and "- #70 done (PR #71)" in shown
    assert "### Added\n- x (#70)" in shown

    assert fragments.main([*root, "fold"]) == 0
    assert "- two (PR #2)\n- #70 done (PR #71)\n" in (tmp_path / "docs" / "STATUS.md").read_text()
    assert "- a (#1)\n- x (#70)\n" in (tmp_path / "CHANGELOG.md").read_text()
    assert not (tmp_path / "changelog.d" / "70-ready.md").exists()
    assert (tmp_path / "changelog.d" / "README.md").exists()


def test_the_old_two_file_layout_is_still_checked_shown_and_folded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo(tmp_path)
    (tmp_path / "docs" / "status.d").mkdir()
    (tmp_path / "docs" / "status.d" / "README.md").write_text("ignored\n")
    (tmp_path / "docs" / "status.d" / "60-old.md").write_text("- sixty the old way\n")
    (tmp_path / "changelog.d" / "60-old.md").write_text("### Fixed\n- fix60 (#60)\n")
    assert fragments.main([*root, "check"]) == 0
    assert "1 in the old" in capsys.readouterr().out
    assert fragments.main([*root, "fold"]) == 0
    status = (tmp_path / "docs" / "STATUS.md").read_text()
    assert "- sixty the old way (#60)\n" in status
    assert "### Fixed\n- fix60 (#60)" in (tmp_path / "CHANGELOG.md").read_text()
    assert not (tmp_path / "docs" / "status.d" / "60-old.md").exists()
    assert not (tmp_path / "changelog.d" / "60-old.md").exists()
    assert (tmp_path / "docs" / "status.d" / "README.md").exists()


def test_add_refuses_a_name_taken_in_the_old_layout(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (tmp_path / "docs" / "status.d").mkdir()
    (tmp_path / "docs" / "status.d" / "70-a.md").write_text("- there\n")
    with pytest.raises(SystemExit, match="exists"):
        fragments.main([*root, "add", "70", "--slug", "a", "--status", "s"])
    assert not (tmp_path / "changelog.d" / "70-a.md").exists()


def test_add_validates_before_writing_anything(tmp_path: Path) -> None:
    root = ["--root", str(tmp_path)]
    with pytest.raises(SystemExit, match="empty"):
        fragments.main([*root, "add", "70", "--slug", "a", "--status", "  ", "--added", "x"])
    with pytest.raises(SystemExit, match="240"):
        fragments.main([*root, "add", "70", "--slug", "a", "--status", "y" * 300])
    with pytest.raises(SystemExit, match="nothing to add"):
        fragments.main([*root, "add", "70", "--slug", "a"])
    assert not (tmp_path / "changelog.d").exists()


def test_check_fails_on_a_bad_fragment(tmp_path: Path) -> None:
    (tmp_path / "changelog.d").mkdir()
    (tmp_path / "changelog.d" / "70-x.md").write_text("no bullet\n")
    assert fragments.main(["--root", str(tmp_path), "check"]) == 1


# plan ticks after merge (#2280): the fragment names its task, the fold ticks the line
# doc-check: ignore-file (the task ids below are made-up test data)

OPEN_LINE = (
    "- [ ] **T285: One symbol map (amendment #2266).** (#2273; size M, class A) · Files: "
    "`src/tradepartner/execution/symbols.py` (new: the map, `risk.*` keys), "
    "`tests/execution/test_symbols.py` · Tests: the map holds after a ticker change · "
    "Depends on: T280a, T283 · Review: safety-reviewer, quant-auditor."
)
COLLAPSED = (
    "- [x] **T285: One symbol map (amendment #2266).** (#2290, PR #2301) · Files: "
    "`src/tradepartner/execution/symbols.py`, `tests/execution/test_symbols.py` · "
    "Depends on: T280a, T283"
)
PLAN = (
    "# Plan: X\n\n## Tasks\n\n"
    "- [x] **T284: Done.** (#1, PR #2) · Files: `a.py` · Depends on: n/a\n"
    f"{OPEN_LINE}\n"
    "- [ ] **T286: Later.** · Files: `b.py` · Depends on: T285\n"
)


def test_a_fragment_names_its_plan_task_and_pr() -> None:
    f = _frag("2290-symbols.md", "task: T285 (PR #2301)\n- #2290 map (PR #2301)\n### Added\n- m\n")
    assert f.tasks == [("T285", 2301)]
    assert f.status == ["- #2290 map (PR #2301)"]
    assert _frag("2290-only.md", "task: T285b (PR #2301)\n").tasks == [("T285b", 2301)]
    assert _frag("70-b.md", "### Fixed\n- f\n").tasks == []


def test_a_task_line_has_one_shape_and_comes_before_any_heading() -> None:
    with pytest.raises(fragments.FragmentError, match="task: Tn"):
        _frag("70-x.md", "task: 285 (PR #1)\n")
    with pytest.raises(fragments.FragmentError, match="task: Tn"):
        _frag("70-x.md", "task: T285\n")
    with pytest.raises(fragments.FragmentError, match="before any heading"):
        _frag("70-x.md", "### Added\n- a\ntask: T285 (PR #1)\n")


def test_collapse_plan_line_keeps_title_paths_and_dependencies() -> None:
    assert fragments.collapse_plan_line(OPEN_LINE, issue=2290, pr=2301) == COLLAPSED
    bare = "- [ ] **T9: Owner thing.** Files: none in the repo · Depends on: n/a."
    assert fragments.collapse_plan_line(bare, issue=5, pr=6) == (
        "- [x] **T9: Owner thing.** (#5, PR #6) · Files: none · Depends on: n/a"
    )
    no_deps = "- [ ] **T10: Solo.** · Files: `x.py`"
    assert fragments.collapse_plan_line(no_deps, issue=5, pr=6).endswith("· Depends on: n/a")


def test_collapse_shortens_paths_in_one_directory_then_refuses_past_the_cap() -> None:
    many = ", ".join(f"`src/tradepartner/execution/module_{i:02d}.py`" for i in range(9))
    line = f"- [ ] **T11: Many.** · Files: {many} · Depends on: n/a"
    out = fragments.collapse_plan_line(line, issue=5, pr=6)
    assert len(out) <= fragments.PLAN_LINE_MAX
    assert "`src/tradepartner/execution/module_00.py`, `module_01.py`" in out
    # Then the dependencies to their ids, then every later path to its file name.
    prose = "T7 (T7 because the recording shows the code paper returns, #1904), T8" + " x" * 60
    spread = ", ".join(f"`src/tradepartner/dir{i}/module_{i:02d}.py`" for i in range(9))
    out = fragments.collapse_plan_line(
        f"- [ ] **T13: Spread.** · Files: {spread} · Depends on: {prose}", issue=5, pr=6
    )
    assert out.endswith("· Depends on: T7, T8")
    assert "`src/tradepartner/dir0/module_00.py`, `module_01.py`" in out
    huge = ", ".join(f"`src/d{i}/a_rather_long_module_name_{i:02d}.py`" for i in range(16))
    with pytest.raises(fragments.FragmentError, match=r"T12.*by hand"):
        fragments.collapse_plan_line(
            f"- [ ] **T12: Huge.** · Files: {huge} · Depends on: n/a", issue=5, pr=6
        )


def test_fold_plans_ticks_each_named_task_once() -> None:
    plans = {"docs/plans/x.md": PLAN}
    frags = [
        _frag("2290-a.md", "task: T285 (PR #2301)\n"),
        _frag("1-old.md", "task: T284 (PR #2)\n"),  # ticked the old way: left as it is
    ]
    out = fragments.fold_plans(plans, frags)
    assert out["docs/plans/x.md"] == PLAN.replace(OPEN_LINE, COLLAPSED)
    assert fragments.fold_plans(out, frags) == out
    with pytest.raises(fragments.FragmentError, match="T999 is not a task"):
        fragments.fold_plans(plans, [_frag("3-x.md", "task: T999 (PR #4)\n")])


def _plan_repo(tmp_path: Path) -> list[str]:
    root = _repo(tmp_path)
    (tmp_path / "docs" / "plans").mkdir()
    (tmp_path / "docs" / "plans" / "x.md").write_text(PLAN)
    return root


def test_add_with_a_task_then_show_and_fold_tick_the_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _plan_repo(tmp_path)
    args = ["add", "2290", "--slug", "map", "--task", "T285", "--pr", "2301", "--added", "m"]
    assert fragments.main([*root, *args]) == 0
    assert (tmp_path / "changelog.d" / "2290-map.md").read_text() == (
        "task: T285 (PR #2301)\n### Added\n- m\n"
    )
    capsys.readouterr()
    assert fragments.main([*root, "check"]) == 0
    assert fragments.main([*root, "show"]) == 0
    assert "Plan ticks (pending" in capsys.readouterr().out
    assert fragments.main([*root, "fold"]) == 0
    assert COLLAPSED in (tmp_path / "docs" / "plans" / "x.md").read_text()
    assert not (tmp_path / "changelog.d" / "2290-map.md").exists()


def test_add_needs_task_and_pr_together(tmp_path: Path) -> None:
    root = _plan_repo(tmp_path)
    with pytest.raises(SystemExit, match="--task and --pr"):
        fragments.main([*root, "add", "2290", "--slug", "m", "--task", "T285", "--added", "m"])
    with pytest.raises(SystemExit, match="--task and --pr"):
        fragments.main([*root, "add", "2290", "--slug", "m", "--pr", "1", "--added", "m"])


def test_check_and_fold_refuse_a_task_no_plan_names(tmp_path: Path) -> None:
    root = _plan_repo(tmp_path)
    (tmp_path / "changelog.d" / "2290-x.md").write_text("task: T999 (PR #1)\n- s\n")
    assert fragments.main([*root, "check"]) == 1
    with pytest.raises(SystemExit, match="T999"):
        fragments.main([*root, "fold"])
    # Nothing was written: STATUS, CHANGELOG and the fragment are as they were.
    assert (tmp_path / "docs" / "STATUS.md").read_text() == STATUS
    assert (tmp_path / "changelog.d" / "2290-x.md").exists()

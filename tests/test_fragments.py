"""Tests for scripts/fragments.py: fragment parsing and folding into STATUS/CHANGELOG."""

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

## Done
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


def _frag(name: str, text: str, kind: str = "status") -> fragments.Fragment:
    parse = (
        fragments.parse_status_fragment if kind == "status" else fragments.parse_changelog_fragment
    )
    return parse(Path(name), text)


def test_status_fragment_is_bullets_only_and_named_by_issue() -> None:
    f = _frag("70-ready.md", "- #70 thing (PR #71)\n- second\n")
    assert f.issue == 70
    assert f.sections[""] == ["- #70 thing (PR #71)", "- second"]
    with pytest.raises(fragments.FragmentError, match="bullet"):
        _frag("70-ready.md", "just prose\n")
    with pytest.raises(fragments.FragmentError, match="empty"):
        _frag("70-ready.md", "\n\n")
    with pytest.raises(fragments.FragmentError, match="name"):
        _frag("ready.md", "- x\n")
    with pytest.raises(fragments.FragmentError, match="name"):
        _frag("70-Ready.md", "- x\n")


def test_changelog_fragment_needs_known_headings_with_bullets() -> None:
    f = _frag("70-ready.md", "### Added\n- a\n\n### Changed\n- c\n", kind="changelog")
    assert f.sections == {"### Added": ["- a"], "### Changed": ["- c"]}
    with pytest.raises(fragments.FragmentError, match="heading"):
        _frag("70-x.md", "- a\n", kind="changelog")
    with pytest.raises(fragments.FragmentError, match="unknown heading"):
        _frag("70-x.md", "### Misc\n- a\n", kind="changelog")
    with pytest.raises(fragments.FragmentError, match="no bullets"):
        _frag("70-x.md", "### Added\n", kind="changelog")


def test_fold_status_appends_after_last_done_bullet_in_issue_order() -> None:
    out = fragments.fold_status(
        STATUS,
        [_frag("80-b.md", "- eighty\n"), _frag("70-a.md", "- seventy\n- seventy bis\n")],
    )
    assert (
        "## Done\n- one (PR #1)\n- two (PR #2)\n- seventy\n- seventy bis\n- eighty\n\n## Teams"
        in out
    )
    assert out.count("- something") == 1  # other sections untouched
    assert fragments.fold_status(STATUS, []) == STATUS


def test_fold_changelog_appends_under_existing_and_new_headings() -> None:
    frags = [
        _frag("80-b.md", "### Added\n- add80\n### Fixed\n- fix80\n", kind="changelog"),
        _frag("70-a.md", "### Added\n- add70\n### Changed\n- chg70\n", kind="changelog"),
    ]
    out = fragments.fold_changelog(CHANGELOG, frags)
    unreleased = out.split("## [0.1.0]")[0]
    assert "### Added\n- a (#1)\n- add70\n- add80\n" in unreleased
    assert "### Changed\n- b (#2)\n- chg70\n" in unreleased
    assert "### Fixed\n- fix80\n" in unreleased
    assert out.endswith("### Added\n- old\n")  # released section untouched
    assert unreleased.index("### Fixed") > unreleased.index("### Changed")


def test_fold_changelog_creates_unreleased_headings_when_empty() -> None:
    text = "# Changelog\n\n## [Unreleased]\n\n## [0.1.0] - x\n### Added\n- old\n"
    out = fragments.fold_changelog(text, [_frag("70-a.md", "### Added\n- new\n", kind="changelog")])
    assert "## [Unreleased]\n### Added\n- new\n\n## [0.1.0] - x\n" in out


def test_add_check_show_and_fold_round_trip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "STATUS.md").write_text(STATUS)
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG)
    (tmp_path / "docs" / "status.d").mkdir()
    (tmp_path / "docs" / "status.d" / "README.md").write_text("ignored\n")

    root = ["--root", str(tmp_path)]
    assert fragments.main([*root, "check"]) == 0
    assert (
        fragments.main(
            [
                *root,
                "add",
                "70",
                "--slug",
                "ready",
                "--status",
                "#70 done (PR #71)",
                "--added",
                "x (#70)",
            ]
        )
        == 0
    )
    assert (tmp_path / "docs" / "status.d" / "70-ready.md").read_text() == "- #70 done (PR #71)\n"
    assert (tmp_path / "changelog.d" / "70-ready.md").read_text() == "### Added\n- x (#70)\n"
    with pytest.raises(SystemExit, match="exists"):
        fragments.main([*root, "add", "70", "--slug", "ready", "--status", "again"])

    assert fragments.main([*root, "show"]) == 0
    assert "- #70 done (PR #71)" in capsys.readouterr().out

    assert fragments.main([*root, "fold"]) == 0
    assert "- two (PR #2)\n- #70 done (PR #71)\n" in (tmp_path / "docs" / "STATUS.md").read_text()
    assert "- a (#1)\n- x (#70)\n" in (tmp_path / "CHANGELOG.md").read_text()
    assert not (tmp_path / "docs" / "status.d" / "70-ready.md").exists()
    assert not (tmp_path / "changelog.d" / "70-ready.md").exists()
    assert (tmp_path / "docs" / "status.d" / "README.md").exists()


def test_check_fails_on_a_bad_fragment(tmp_path: Path) -> None:
    (tmp_path / "changelog.d").mkdir()
    (tmp_path / "changelog.d" / "70-x.md").write_text("no heading\n")
    assert fragments.main(["--root", str(tmp_path), "check"]) == 1

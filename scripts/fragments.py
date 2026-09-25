#!/usr/bin/env python3
"""Per-PR bookkeeping fragments, so parallel PRs never edit the same shared lines.

Every PR used to append one line to ``docs/STATUS.md`` ("Done") and one to
``CHANGELOG.md`` ("[Unreleased]") at the same anchor. Git cannot merge two insertions at
one spot, so every merge to ``main`` conflicted every other open PR. Instead, a PR now adds
**new files**::

    docs/status.d/<issue>-<slug>.md      one or more "- ..." bullets for STATUS "Done"
    changelog.d/<issue>-<slug>.md        "### Added|Changed|Fixed|Removed" headings + bullets

New files never conflict. ``fold`` moves every fragment into the shared files in issue
order and deletes it; it runs inside a PR that already edits those files for another
reason (doc-keeper, a plan amendment, the phase-close PR), never in a PR of its own.

Usage::

    uv run python scripts/fragments.py add 70 --slug ready-command \\
        --status "#70 Fragments and `ready` (PR #71)" --added "Process: ... (#70)"
    uv run python scripts/fragments.py check      # shape of every fragment (CI runs this)
    uv run python scripts/fragments.py show       # pending entries, for reading STATUS
    uv run python scripts/fragments.py fold       # merge into STATUS/CHANGELOG, delete
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

STATUS_DIR = Path("docs/status.d")
CHANGELOG_DIR = Path("changelog.d")
STATUS_FILE = Path("docs/STATUS.md")
CHANGELOG_FILE = Path("CHANGELOG.md")
STATUS_SECTION = "## Done"
CHANGELOG_SECTION = "## [Unreleased]"
CHANGELOG_HEADINGS = ("### Added", "### Changed", "### Deprecated", "### Removed", "### Fixed")
FRAGMENT_NAME_RE = re.compile(r"^(?P<issue>\d+)-(?P<slug>[a-z0-9][a-z0-9-]*)\.md$")
BULLET_RE = re.compile(r"^- \S")
SKIP_NAMES = frozenset({"README.md", ".gitkeep"})


@dataclass(frozen=True)
class Fragment:
    """One fragment file, parsed. ``sections`` maps a heading ("" for STATUS) to bullets."""

    path: Path
    issue: int
    sections: dict[str, list[str]] = field(default_factory=dict)


class FragmentError(ValueError):
    """A fragment has the wrong name or shape."""


# ── parsing (pure) ──────────────────────────────────────────────────────────────


def parse_status_fragment(path: Path, text: str) -> Fragment:
    """A STATUS fragment is bullets only, at least one."""
    issue = _issue_from_name(path)
    bullets = _bullets_only(path, text.strip().splitlines())
    return Fragment(path, issue, {"": bullets})


def parse_changelog_fragment(path: Path, text: str) -> Fragment:
    """A CHANGELOG fragment is one or more Keep-a-Changelog headings, each with bullets."""
    issue = _issue_from_name(path)
    sections: dict[str, list[str]] = {}
    heading: str | None = None
    for raw in text.strip().splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("### "):
            if line not in CHANGELOG_HEADINGS:
                raise FragmentError(f"{path}: unknown heading {line!r}; use {CHANGELOG_HEADINGS}")
            heading = line
            sections.setdefault(heading, [])
            continue
        if heading is None:
            raise FragmentError(f"{path}: bullets must follow a '### Added'-style heading")
        if not BULLET_RE.match(line):
            raise FragmentError(f"{path}: expected a '- ...' bullet, got {line!r}")
        sections[heading].append(line)
    if not sections:
        raise FragmentError(f"{path}: empty fragment")
    for heading, bullets in sections.items():
        if not bullets:
            raise FragmentError(f"{path}: heading {heading!r} has no bullets")
    return Fragment(path, issue, sections)


def _issue_from_name(path: Path) -> int:
    m = FRAGMENT_NAME_RE.match(path.name)
    if not m:
        raise FragmentError(f"{path}: name must be <issue>-<slug>.md (lowercase, digits, dashes)")
    return int(m.group("issue"))


def _bullets_only(path: Path, lines: Sequence[str]) -> list[str]:
    bullets = [ln.rstrip() for ln in lines if ln.strip()]
    if not bullets:
        raise FragmentError(f"{path}: empty fragment")
    for ln in bullets:
        if not BULLET_RE.match(ln):
            raise FragmentError(f"{path}: expected a '- ...' bullet, got {ln!r}")
    return bullets


# ── folding (pure) ──────────────────────────────────────────────────────────────


def _section_bounds(lines: Sequence[str], heading: str, level: str = "## ") -> tuple[int, int]:
    """Return ``(start, end)`` line indexes of the section body under ``heading``.

    ``end`` is the index of the next heading of the same or higher level, or ``len(lines)``.
    """
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == heading)
    except StopIteration:
        raise FragmentError(f"heading {heading!r} not found") from None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if ln.startswith(level) or (level == "### " and ln.startswith("## ")):
            end = i
            break
    return start + 1, end


def _last_bullet_index(lines: Sequence[str], start: int, end: int) -> int:
    """Index just after the last bullet in ``lines[start:end]`` (or ``start`` if none)."""
    last = start
    for i in range(start, end):
        if BULLET_RE.match(lines[i]):
            last = i + 1
    return last


def fold_status(text: str, fragments: Sequence[Fragment]) -> str:
    """Append every fragment's bullets after the last bullet of the "## Done" list."""
    if not fragments:
        return text
    lines = text.splitlines()
    start, end = _section_bounds(lines, STATUS_SECTION)
    at = _last_bullet_index(lines, start, end)
    new = [b for f in sorted(fragments, key=lambda f: f.issue) for b in f.sections[""]]
    lines[at:at] = new
    return "\n".join(lines) + "\n"


def fold_changelog(text: str, fragments: Sequence[Fragment]) -> str:
    """Append bullets under the matching ``### `` heading of "[Unreleased]", creating it."""
    if not fragments:
        return text
    lines = text.splitlines()
    for heading in CHANGELOG_HEADINGS:
        new = [
            b for f in sorted(fragments, key=lambda f: f.issue) for b in f.sections.get(heading, [])
        ]
        if not new:
            continue
        u_start, u_end = _section_bounds(lines, CHANGELOG_SECTION)
        sub = lines[u_start:u_end]
        if heading in (ln.strip() for ln in sub):
            h_start, h_end = _section_bounds(sub, heading, level="### ")
            at = u_start + _last_bullet_index(sub, h_start, h_end)
            lines[at:at] = new
        else:
            at = u_start + _trim_trailing_blank(sub)
            block = ([""] if at > u_start and lines[at - 1].strip() else []) + [heading, *new]
            if at < u_end and lines[at].strip():
                block.append("")
            lines[at:at] = block
    return "\n".join(lines) + "\n"


def _trim_trailing_blank(sub: Sequence[str]) -> int:
    """Index in ``sub`` where the trailing blank lines begin."""
    i = len(sub)
    while i > 0 and not sub[i - 1].strip():
        i -= 1
    return i


# ── files ───────────────────────────────────────────────────────────────────────


def _fragment_files(root: Path, directory: Path) -> list[Path]:
    d = root / directory
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and p.name not in SKIP_NAMES)


def load_fragments(root: Path) -> tuple[list[Fragment], list[Fragment]]:
    """Parse every fragment under ``root``; raises ``FragmentError`` on the first bad one."""
    status = [parse_status_fragment(p, p.read_text()) for p in _fragment_files(root, STATUS_DIR)]
    changelog = [
        parse_changelog_fragment(p, p.read_text()) for p in _fragment_files(root, CHANGELOG_DIR)
    ]
    return status, changelog


def write_fragment(root: Path, directory: Path, issue: int, slug: str, body: str) -> Path:
    path = root / directory / f"{issue}-{slug}.md"
    if path.exists():
        raise FragmentError(f"{path} exists; edit it instead of adding another")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.rstrip("\n") + "\n")
    return path


def cmd_add(
    root: Path,
    issue: int,
    slug: str,
    *,
    status: Sequence[str],
    added: Sequence[str],
    changed: Sequence[str],
    fixed: Sequence[str],
    removed: Sequence[str],
) -> int:
    if not slug or not FRAGMENT_NAME_RE.match(f"{issue}-{slug}.md"):
        raise SystemExit("--slug must be lowercase letters, digits and dashes")
    written: list[Path] = []
    if status:
        body = "\n".join(_as_bullet(s) for s in status)
        written.append(write_fragment(root, STATUS_DIR, issue, slug, body))
    parts: list[str] = []
    for heading, items in (
        ("### Added", added),
        ("### Changed", changed),
        ("### Fixed", fixed),
        ("### Removed", removed),
    ):
        if items:
            parts.append(heading)
            parts.extend(_as_bullet(s) for s in items)
    if parts:
        written.append(write_fragment(root, CHANGELOG_DIR, issue, slug, "\n".join(parts)))
    if not written:
        raise SystemExit("nothing to add: pass --status and/or --added/--changed/--fixed/--removed")
    load_fragments(root)  # validate what we just wrote
    for p in written:
        print(f"wrote {p.relative_to(root)}")
    return 0


def _as_bullet(text: str) -> str:
    text = text.strip()
    return text if text.startswith("- ") else f"- {text}"


def cmd_check(root: Path) -> int:
    try:
        status, changelog = load_fragments(root)
    except FragmentError as exc:
        print(f"fragment check FAILED: {exc}")
        return 1
    print(f"fragments ok: {len(status)} status, {len(changelog)} changelog")
    return 0


def cmd_show(root: Path) -> int:
    status, changelog = load_fragments(root)
    if not status and not changelog:
        print("no pending fragments")
        return 0
    if status:
        print(f"{STATUS_SECTION} (pending, from {STATUS_DIR}/)")
        for f in sorted(status, key=lambda f: f.issue):
            print("\n".join(f.sections[""]))
    if changelog:
        print(f"\n{CHANGELOG_SECTION} (pending, from {CHANGELOG_DIR}/)")
        for heading in CHANGELOG_HEADINGS:
            items = [
                b
                for f in sorted(changelog, key=lambda f: f.issue)
                for b in f.sections.get(heading, [])
            ]
            if items:
                print(heading)
                print("\n".join(items))
    return 0


def cmd_fold(root: Path, *, keep: bool = False) -> int:
    status, changelog = load_fragments(root)
    if not status and not changelog:
        print("no pending fragments")
        return 0
    status_path = root / STATUS_FILE
    changelog_path = root / CHANGELOG_FILE
    status_path.write_text(fold_status(status_path.read_text(), status))
    changelog_path.write_text(fold_changelog(changelog_path.read_text(), changelog))
    if not keep:
        for f in [*status, *changelog]:
            f.path.unlink()
    print(
        f"folded {len(status)} status and {len(changelog)} changelog fragments into "
        f"{STATUS_FILE} and {CHANGELOG_FILE}" + (" (fragments kept)" if keep else "")
    )
    return 0


# ── entry point ─────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fragments.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", type=Path, default=None, help="repo root (default: cwd)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("add", help="write this PR's fragment file(s)")
    p.add_argument("issue", type=int)
    p.add_argument("--slug", required=True, help="short lowercase slug for the file name")
    p.add_argument("--status", action="append", default=[], help="STATUS Done bullet (repeatable)")
    p.add_argument("--added", action="append", default=[], help="CHANGELOG Added bullet")
    p.add_argument("--changed", action="append", default=[], help="CHANGELOG Changed bullet")
    p.add_argument("--fixed", action="append", default=[], help="CHANGELOG Fixed bullet")
    p.add_argument("--removed", action="append", default=[], help="CHANGELOG Removed bullet")
    sub.add_parser("check", help="validate every fragment's name and shape")
    sub.add_parser("show", help="print pending entries")
    p = sub.add_parser("fold", help="merge fragments into STATUS.md and CHANGELOG.md")
    p.add_argument("--keep", action="store_true", help="do not delete the fragment files")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = (args.root or Path.cwd()).resolve()
    try:
        match args.command:
            case "add":
                return cmd_add(
                    root,
                    args.issue,
                    args.slug,
                    status=args.status,
                    added=args.added,
                    changed=args.changed,
                    fixed=args.fixed,
                    removed=args.removed,
                )
            case "check":
                return cmd_check(root)
            case "show":
                return cmd_show(root)
            case "fold":
                return cmd_fold(root, keep=args.keep)
    except FragmentError as exc:
        raise SystemExit(str(exc)) from None
    return 2


if __name__ == "__main__":
    sys.exit(main())

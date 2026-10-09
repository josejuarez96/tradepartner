#!/usr/bin/env python3
"""Per-PR bookkeeping fragments, so parallel PRs never edit the same shared lines.

A PR records its bookkeeping in **one new file**, never in the shared files::

    changelog.d/<issue>-<slug>.md

    - <one STATUS "Recently done" line, optional, at most 240 characters>
    ### Added
    - <CHANGELOG [Unreleased] bullet>
    ### Fixed
    - ...

A bullet before any heading is the STATUS line; the ``### Added|Changed|Deprecated|
Removed|Fixed`` headings carry the CHANGELOG bullets. New files never conflict. ``fold``
appends the CHANGELOG bullets under ``[Unreleased]`` in issue order, appends the STATUS lines
to ``## Recently done`` and keeps only the last ``STATUS_RECENT_N`` there (older lines are
dropped: CHANGELOG and git history keep them), then deletes the fragments. It runs inside a
PR that already edits those files (doc-keeper, a plan amendment, the phase-close PR).

Transition (#351): ``docs/status.d/<issue>-<slug>.md`` files (bullets only), the old layout
beside ``changelog.d/``, are still read, checked, shown and folded, so PRs opened before
the change keep working. A STATUS line without a ``#<number>`` gets ``(#<issue>)``, and one
longer than the limit is cut at a word boundary, when it is folded.

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

FRAGMENT_DIR = Path("changelog.d")
LEGACY_STATUS_DIR = Path("docs/status.d")
STATUS_FILE = Path("docs/STATUS.md")
CHANGELOG_FILE = Path("CHANGELOG.md")
STATUS_SECTION = "## Recently done"
#: How many "Recently done" lines STATUS keeps after a fold (#351).
STATUS_RECENT_N = 10
#: A new STATUS line's length limit; longer legacy lines are cut when folded (#351).
STATUS_LINE_MAX = 240
CHANGELOG_SECTION = "## [Unreleased]"
CHANGELOG_HEADINGS = ("### Added", "### Changed", "### Deprecated", "### Removed", "### Fixed")
FRAGMENT_NAME_RE = re.compile(r"^(?P<issue>\d+)-(?P<slug>[a-z0-9][a-z0-9-]*)\.md$")
BULLET_RE = re.compile(r"^- \S")
LINK_RE = re.compile(r"#\d+")
SKIP_NAMES = frozenset({"README.md", ".gitkeep"})

# Old names, kept so tools written against the two-directory layout still import.
STATUS_DIR = LEGACY_STATUS_DIR
CHANGELOG_DIR = FRAGMENT_DIR


@dataclass(frozen=True)
class Fragment:
    """One fragment file, parsed: its STATUS lines and its CHANGELOG sections."""

    path: Path
    issue: int
    status: list[str] = field(default_factory=list)
    sections: dict[str, list[str]] = field(default_factory=dict)


class FragmentError(ValueError):
    """A fragment has the wrong name or shape."""


# ── parsing (pure) ──────────────────────────────────────────────────────────────


def parse_fragment(path: Path, text: str) -> Fragment:
    """A fragment: at most one STATUS bullet before any heading (at most
    ``STATUS_LINE_MAX`` characters), then Keep-a-Changelog headings with bullets.
    It needs a STATUS line, a CHANGELOG bullet, or both."""
    issue = _issue_from_name(path)
    status: list[str] = []
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
        if not BULLET_RE.match(line):
            raise FragmentError(f"{path}: expected a '- ...' bullet, got {line!r}")
        if heading is None:
            status.append(line)
        else:
            sections[heading].append(line)
    if len(status) > 1:
        raise FragmentError(f"{path}: one STATUS line per fragment, got {len(status)}")
    if status and len(status[0]) > STATUS_LINE_MAX:
        raise FragmentError(
            f"{path}: the STATUS line is {len(status[0])} characters; keep it to "
            f"{STATUS_LINE_MAX} and put the detail in the CHANGELOG bullet or the PR"
        )
    for name, bullets in sections.items():
        if not bullets:
            raise FragmentError(f"{path}: heading {name!r} has no bullets")
    if not status and not sections:
        raise FragmentError(f"{path}: empty fragment")
    return Fragment(path, issue, status, sections)


def parse_legacy_status_fragment(path: Path, text: str) -> Fragment:
    """An old-layout ``docs/status.d`` fragment: bullets only, at least one."""
    issue = _issue_from_name(path)
    return Fragment(path, issue, _bullets_only(path, text.strip().splitlines()), {})


# The pre-#351 names, for callers and tests of the old layout.
parse_status_fragment = parse_legacy_status_fragment


def parse_changelog_fragment(path: Path, text: str) -> Fragment:
    """An old-layout CHANGELOG fragment (no STATUS line); the new parser accepts it."""
    return parse_fragment(path, text)


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


def recent_line(line: str, issue: int) -> str:
    """A STATUS line as it enters "Recently done": linked to its issue (``(#<issue>)``
    appended when it names no ``#<number>``) and cut to ``STATUS_LINE_MAX`` characters
    at a word boundary, the link kept."""
    link = "" if LINK_RE.search(line) else f" (#{issue})"
    if len(line) + len(link) <= STATUS_LINE_MAX:
        return line + link
    tail = link or f" (#{issue})"
    room = STATUS_LINE_MAX - len(tail) - 1
    cut = line[:room].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{cut}…{tail}"


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
    """Index just after the last bullet in ``lines[start:end]`` (or ``start`` if none).

    An indented line directly under a bullet is its continuation and stays with it.
    """
    last = start
    for i in range(start, end):
        ln = lines[i]
        if BULLET_RE.match(ln) or (last == i and ln[:1] in (" ", "\t") and ln.strip()):
            last = i + 1
    return last


def _bullet_blocks(lines: Sequence[str]) -> list[list[str]]:
    """``lines`` (a list's bullets) grouped as bullet plus indented continuation lines."""
    blocks: list[list[str]] = []
    for ln in lines:
        if BULLET_RE.match(ln):
            blocks.append([ln])
        elif blocks and ln[:1] in (" ", "\t") and ln.strip():
            blocks[-1].append(ln)
    return blocks


def fold_status(text: str, fragments: Sequence[Fragment], keep: int = STATUS_RECENT_N) -> str:
    """Append every fragment's STATUS lines (issue order, linked and cut by
    ``recent_line``) to "## Recently done", then keep only the last ``keep`` bullets
    there. Lines before the first bullet (a note) and every other section are kept."""
    new = [
        recent_line(line, f.issue)
        for f in sorted(fragments, key=lambda f: f.issue)
        for line in f.status
    ]
    lines = text.splitlines()
    start, end = _section_bounds(lines, STATUS_SECTION)
    first = next((i for i in range(start, end) if BULLET_RE.match(lines[i])), None)
    last = _last_bullet_index(lines, start, end)
    if first is None:
        first = last = start + _trim_trailing_blank(lines[start:end])
    blocks = _bullet_blocks(lines[first:last]) + [[b] for b in new]
    kept = [ln for block in blocks[-keep:] for ln in block] if keep > 0 else []
    lines[first:last] = kept
    return "\n".join(lines) + "\n"


def fold_changelog(text: str, fragments: Sequence[Fragment]) -> str:
    """Append bullets under the matching ``### `` heading of "[Unreleased]", creating it."""
    if not any(f.sections for f in fragments):
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


def load_fragments(root: Path) -> list[Fragment]:
    """Every fragment under ``root``, new and legacy layout, in issue order;
    raises ``FragmentError`` on the first bad one."""
    found = [parse_fragment(p, p.read_text()) for p in _fragment_files(root, FRAGMENT_DIR)]
    found += [
        parse_legacy_status_fragment(p, p.read_text())
        for p in _fragment_files(root, LEGACY_STATUS_DIR)
    ]
    return sorted(found, key=lambda f: (f.issue, str(f.path)))


def cmd_add(
    root: Path,
    issue: int,
    slug: str,
    *,
    status: str | None,
    added: Sequence[str],
    changed: Sequence[str],
    fixed: Sequence[str],
    removed: Sequence[str],
) -> int:
    if not slug or not FRAGMENT_NAME_RE.match(f"{issue}-{slug}.md"):
        raise SystemExit("--slug must be lowercase letters, digits and dashes")
    name = f"{issue}-{slug}.md"
    parts: list[str] = []
    if status is not None:
        parts.append(_as_bullet(status))
    for heading, items in (
        ("### Added", added),
        ("### Changed", changed),
        ("### Fixed", fixed),
        ("### Removed", removed),
    ):
        if items:
            parts.append(heading)
            parts.extend(_as_bullet(s) for s in items)
    if not parts:
        raise SystemExit("nothing to add: pass --status and/or --added/--changed/--fixed/--removed")
    body = "\n".join(parts)
    parse_fragment(FRAGMENT_DIR / name, body)  # validate before writing anything
    for directory in (FRAGMENT_DIR, LEGACY_STATUS_DIR):
        if (root / directory / name).exists():
            raise FragmentError(f"{directory / name} exists; edit it instead of adding another")
    path = root / FRAGMENT_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + "\n")
    print(f"wrote {path.relative_to(root)}")
    return 0


def _as_bullet(text: str) -> str:
    text = text.strip()
    if not text or text == "-":
        raise FragmentError("empty bullet")
    return text if text.startswith("- ") else f"- {text}"


def cmd_check(root: Path) -> int:
    try:
        found = load_fragments(root)
    except FragmentError as exc:
        print(f"fragment check FAILED: {exc}")
        return 1
    legacy = sum(1 for f in found if f.path.parent.name == LEGACY_STATUS_DIR.name)
    print(f"fragments ok: {len(found)} ({legacy} in the old {LEGACY_STATUS_DIR}/ layout)")
    return 0


def cmd_show(root: Path) -> int:
    found = load_fragments(root)
    if not found:
        print("no pending fragments")
        return 0
    status = [recent_line(line, f.issue) for f in found for line in f.status]
    if status:
        print(f"{STATUS_SECTION} (pending, from {FRAGMENT_DIR}/)")
        print("\n".join(status))
    if any(f.sections for f in found):
        print(f"\n{CHANGELOG_SECTION} (pending, from {FRAGMENT_DIR}/)")
        for heading in CHANGELOG_HEADINGS:
            items = [b for f in found for b in f.sections.get(heading, [])]
            if items:
                print(heading)
                print("\n".join(items))
    return 0


def cmd_fold(root: Path, *, keep: bool = False) -> int:
    found = load_fragments(root)
    if not found:
        print("no pending fragments")
        return 0
    status_path = root / STATUS_FILE
    changelog_path = root / CHANGELOG_FILE
    status_path.write_text(fold_status(status_path.read_text(), found))
    changelog_path.write_text(fold_changelog(changelog_path.read_text(), found))
    if not keep:
        for f in found:
            f.path.unlink()
    lines = sum(len(f.status) for f in found)
    print(
        f"folded {len(found)} fragments ({lines} STATUS lines, the last {STATUS_RECENT_N} kept) "
        f"into {STATUS_FILE} and {CHANGELOG_FILE}" + (" (fragments kept)" if keep else "")
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
    p = sub.add_parser("add", help="write this PR's fragment file")
    p.add_argument("issue", type=int)
    p.add_argument("--slug", required=True, help="short lowercase slug for the file name")
    p.add_argument(
        "--status",
        default=None,
        help=f'the one STATUS "Recently done" line (at most {STATUS_LINE_MAX} characters)',
    )
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

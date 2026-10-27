#!/usr/bin/env python3
"""Per-PR bookkeeping fragments, so parallel PRs never edit the same shared lines.

A PR records its bookkeeping in **one new file**, never in the shared files::

    changelog.d/<issue>-<slug>.md

    task: T285 (PR #2301)
    - <one STATUS "Recently done" line, optional, at most 240 characters>
    ### Added
    - <CHANGELOG [Unreleased] bullet>
    ### Fixed
    - ...

A ``task: Tn (PR #n)`` line, before any heading, names the plan task the PR finishes
(#2280): the PR never edits ``docs/plans/``, the fold ticks and collapses that line.
A bullet before any heading is the STATUS line; the ``### Added|Changed|Deprecated|
Removed|Fixed`` headings carry the CHANGELOG bullets. New files never conflict. ``fold``
appends the CHANGELOG bullets under ``[Unreleased]`` in issue order, appends the STATUS lines
to ``## Recently done`` and keeps only the last ``STATUS_RECENT_N`` there (older lines are
dropped: CHANGELOG and git history keep them), ticks and collapses each named task's plan
line (``- [x] **Tn: Title** (#issue, PR #n) · Files: <paths> · Depends on: <unchanged>``,
#353), then deletes the fragments. ``team.py`` counts a merged fragment's task as done, so
nothing waits on the fold. It runs inside a
PR that already edits those files (doc-keeper, a plan amendment, the phase-close PR).

Transition (#351): ``docs/status.d/<issue>-<slug>.md`` files (bullets only), the old layout
beside ``changelog.d/``, are still read, checked, shown and folded, so PRs opened before
the change keep working. A STATUS line without a ``#<number>`` gets ``(#<issue>)``, and one
longer than the limit is cut at a word boundary, when it is folded.

Usage::

    uv run python scripts/fragments.py add 70 --slug ready-command \\
        --status "#70 Fragments and `ready` (PR #71)" --added "Process: ... (#70)"
    uv run python scripts/fragments.py add 2290 --slug symbols --task T285 --pr 2301 ...
    uv run python scripts/fragments.py check      # shape of every fragment (CI runs this)
    uv run python scripts/fragments.py show       # pending entries, for reading STATUS
    uv run python scripts/fragments.py fold       # merge into STATUS/CHANGELOG, delete
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
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
PLANS_DIR = Path("docs/plans")
#: A finished plan line's length cap; ``tests/test_docs_budget.py`` enforces it (#353).
PLAN_LINE_MAX = 400
#: A fragment's plan-task line (#2280); ``team.py`` and ``ready_pr.py`` read the same shape.
TASK_LINE_RE = re.compile(r"^task: (?P<id>T\d+[a-z]?\d?) \(PR #(?P<pr>\d+)\)$")
PLAN_TASK_RE = re.compile(
    r"^- \[(?P<done>[ x])\] (?P<bold>\*\*(?P<id>T\d+[a-z]?\d?)(?: \(owner\))?: .*?\*\*)"
)
PLAN_FILES_RE = re.compile(r"Files:\s*(?P<seg>[^·]*)")
PLAN_DEPENDS_RE = re.compile(r"Depends on:\s*(?P<seg>[^·]*)")
TASK_REF_RE = re.compile(r"\bT\d+[a-z]?\d?\b")
PAREN_RE = re.compile(r"\([^()]*\)")
PATH_TOKEN_RE = re.compile(r"`([^`]+)`")
FILE_NAME_RE = re.compile(r"^[\w.-]+\.\w+$")
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
    #: ``(task id, PR number)`` pairs from ``task:`` lines (#2280).
    tasks: list[tuple[str, int]] = field(default_factory=list)


class FragmentError(ValueError):
    """A fragment has the wrong name or shape."""


# ── parsing (pure) ──────────────────────────────────────────────────────────────


def parse_fragment(path: Path, text: str) -> Fragment:
    """A fragment: at most one STATUS bullet before any heading (at most
    ``STATUS_LINE_MAX`` characters), then Keep-a-Changelog headings with bullets.
    It needs a STATUS line, a CHANGELOG bullet, a ``task: Tn (PR #n)`` line (#2280),
    or any mix; task lines come before any heading."""
    issue = _issue_from_name(path)
    status: list[str] = []
    sections: dict[str, list[str]] = {}
    tasks: list[tuple[str, int]] = []
    heading: str | None = None
    for raw in text.strip().splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("task:"):
            if heading is not None:
                raise FragmentError(f"{path}: the task line goes before any heading")
            m = TASK_LINE_RE.match(line)
            if not m:
                raise FragmentError(f"{path}: expected `task: Tn (PR #n)`, got {line!r}")
            tasks.append((m.group("id"), int(m.group("pr"))))
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
    if not status and not sections and not tasks:
        raise FragmentError(f"{path}: empty fragment")
    return Fragment(path, issue, status, sections, tasks)


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


def _files_segment(line: str) -> list[str]:
    """The backticked paths of a plan line's ``Files:`` segment, prose in parentheses
    (``(new: the map, `risk.*` keys)``) dropped."""
    m = PLAN_FILES_RE.search(line)
    if not m:
        return []
    seg = m.group("seg")
    while (shorter := PAREN_RE.sub("", seg)) != seg:
        seg = shorter
    return [t for t in PATH_TOKEN_RE.findall(seg) if "/" in t or FILE_NAME_RE.match(t) is not None]


def _shorten(paths: Sequence[str]) -> list[str]:
    """A path in the previous path's directory shown by its file name only."""
    out: list[str] = []
    for i, p in enumerate(paths):
        same_dir = i > 0 and "/" in p and p.rpartition("/")[0] == paths[i - 1].rpartition("/")[0]
        out.append(p.rpartition("/")[2] if same_dir else p)
    return out


def collapse_plan_line(line: str, *, issue: int, pr: int) -> str:
    """An open plan line in its finished shape (#353): ``- [x] **Tn: Title** (#issue, PR
    #pr) · Files: <paths> · Depends on: <unchanged>``. While the line would pass
    ``PLAN_LINE_MAX`` it is shortened in steps: a path in the previous path's directory to
    its file name; the dependencies to their task ids (all ``team.py`` reads); every path
    after the first to its file name. Past the cap even then, it raises."""
    m = PLAN_TASK_RE.match(line)
    if not m:
        raise FragmentError(f"not a plan task line: {line[:80]!r}")
    deps_m = PLAN_DEPENDS_RE.search(line)
    deps = (deps_m.group("seg").strip().rstrip(".").strip() if deps_m else "") or "n/a"
    dep_ids = ", ".join(dict.fromkeys(TASK_REF_RE.findall(deps))) or "n/a"
    paths = _files_segment(line)
    short = _shorten(paths)
    names = paths[:1] + [p.rstrip("/").rpartition("/")[2] for p in paths[1:]]

    def render(ps: Sequence[str], ds: str) -> str:
        files = ", ".join(f"`{p}`" for p in ps) or "none"
        return f"- [x] {m.group('bold')} (#{issue}, PR #{pr}) · Files: {files} · Depends on: {ds}"

    for out in (
        render(paths, deps),
        render(short, deps),
        render(short, dep_ids),
        render(names, dep_ids),
    ):
        if len(out) <= PLAN_LINE_MAX:
            return out
    raise FragmentError(
        f"{m.group('id')}: the collapsed line is {len(out)} characters, over "
        f"{PLAN_LINE_MAX}; collapse it by hand, then fold again"
    )


def plan_task_ids(plans: Mapping[str, str]) -> frozenset[str]:
    """Every task id a plan line names, ticked or not."""
    return frozenset(
        m.group("id")
        for text in plans.values()
        for line in text.split("\n")
        if (m := PLAN_TASK_RE.match(line))
    )


def fold_plans(plans: Mapping[str, str], fragments: Sequence[Fragment]) -> dict[str, str]:
    """Tick and collapse the plan line of every task a fragment names (#2280). A line
    already ticked (the old way, or by hand) is left as it is; a task no plan names raises."""
    out = {name: text.split("\n") for name, text in plans.items()}
    for f in sorted(fragments, key=lambda f: f.issue):
        for task, pr in f.tasks:
            hit = next(
                (
                    (name, i)
                    for name, lines in out.items()
                    for i, ln in enumerate(lines)
                    if (m := PLAN_TASK_RE.match(ln)) and m.group("id") == task
                ),
                None,
            )
            if hit is None:
                raise FragmentError(f"{f.path}: {task} is not a task in {PLANS_DIR}/")
            name, i = hit
            if out[name][i].startswith("- [ ] "):
                out[name][i] = collapse_plan_line(out[name][i], issue=f.issue, pr=pr)
    return {name: "\n".join(lines) for name, lines in out.items()}


# ── files ───────────────────────────────────────────────────────────────────────


def _fragment_files(root: Path, directory: Path) -> list[Path]:
    d = root / directory
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and p.name not in SKIP_NAMES)


def read_plans(root: Path) -> dict[str, str]:
    """Plan files under ``root``, by repo-relative path."""
    d = root / PLANS_DIR
    if not d.is_dir():
        return {}
    return {p.relative_to(root).as_posix(): p.read_text() for p in sorted(d.glob("*.md"))}


def check_tasks(root: Path, found: Sequence[Fragment]) -> None:
    """Every task a fragment names is a task in some plan under ``root``."""
    known = plan_task_ids(read_plans(root))
    for f in found:
        for task, _ in f.tasks:
            if task not in known:
                raise FragmentError(f"{f.path}: {task} is not a task in {PLANS_DIR}/")


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
    task: str | None = None,
    pr: int | None = None,
) -> int:
    if (task is None) != (pr is None):
        raise SystemExit("pass --task and --pr together: `task: Tn (PR #n)` (#2280)")
    if not slug or not FRAGMENT_NAME_RE.match(f"{issue}-{slug}.md"):
        raise SystemExit("--slug must be lowercase letters, digits and dashes")
    name = f"{issue}-{slug}.md"
    parts: list[str] = []
    if task is not None:
        parts.append(f"task: {task} (PR #{pr})")
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
        raise SystemExit(
            "nothing to add: pass --task/--pr, --status and/or --added/--changed/--fixed/--removed"
        )
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
        check_tasks(root, found)
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
    ticks = [f"- {t} (#{f.issue}, PR #{pr})" for f in found for t, pr in f.tasks]
    if ticks:
        print(f"\nPlan ticks (pending, from {FRAGMENT_DIR}/; done for team.py already)")
        print("\n".join(ticks))
    return 0


def cmd_fold(root: Path, *, keep: bool = False) -> int:
    found = load_fragments(root)
    if not found:
        print("no pending fragments")
        return 0
    status_path = root / STATUS_FILE
    changelog_path = root / CHANGELOG_FILE
    # Everything is computed before anything is written: a bad task leaves the tree as it was.
    plans = read_plans(root) if any(f.tasks for f in found) else {}
    new_plans = fold_plans(plans, found)
    new_status = fold_status(status_path.read_text(), found)
    new_changelog = fold_changelog(changelog_path.read_text(), found)
    status_path.write_text(new_status)
    changelog_path.write_text(new_changelog)
    for name, text in new_plans.items():
        if text != plans[name]:
            (root / name).write_text(text)
    if not keep:
        for f in found:
            f.path.unlink()
    lines = sum(len(f.status) for f in found)
    ticked = sum(len(f.tasks) for f in found)
    print(
        f"folded {len(found)} fragments ({lines} STATUS lines, the last {STATUS_RECENT_N} kept; "
        f"{ticked} plan tasks ticked) "
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
    p.add_argument("--task", default=None, help="the plan task this PR finishes (T285); needs --pr")
    p.add_argument("--pr", type=int, default=None, help="this PR's number, with --task")
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
                    task=args.task,
                    pr=args.pr,
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

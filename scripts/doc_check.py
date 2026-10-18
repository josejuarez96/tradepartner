#!/usr/bin/env python3
"""Two documentation checks for the docs efficiency pass (#1751).

``cite``: every ID cited where code and open work depend on it still resolves. It reads the
open (``- [ ]``) task lines of ``docs/plans/*.md`` and every ``.py`` file under ``src/``
and ``tests/``, and checks four kinds of citation:

- a task number (``T45``, ``T190b``; ``T0`` and ``T04`` are not task numbers) names a task
  line in some plan (open or finished);
- an ADR number (``ADR 0023``, ``ADR-17``) names a file ``docs/decisions/<NNNN>-*.md``;
- a claim ID (``HO-10``, ``ER-4``, ``EP-P11``, ``G2-A``) whose prefix is a claim-register
  prefix names an ``id`` in ``docs/research/claims.toml``;
- a requirement (``req 5``) names a numbered item under a spec's ``## Requirements``.
  The spec is the one named just before it on the same line (``strategy-lab spec req 4``,
  ``specs/backtest.md req 3``, within ``SPEC_SCOPE_CHARS``); else, on a plan line, the spec
  with the plan's own name; else any spec with that requirement number (a weak check: most
  ``req N`` in ``src/`` and ``tests/`` name no spec). A retired requirement keeps its
  number, so it still resolves.

A line that holds ``doc-check: ignore`` (test data such as made-up task ids) is skipped,
and so is a whole file that holds ``doc-check: ignore-file`` (this check's own tests).
It prints each unresolved citation as ``path:line: <kind> <id>`` and exits 1 when there
is one, 0 otherwise.

``size``: per-file size budgets (KB, 1 KB = 1,000 bytes) for the always-loaded
instructions and the ways-of-working docs, from the owner's scope of 2026-10-18. It prints
a ``WARN`` line for each file over its budget and always exits 0: the owner chose
warn-only budgets. ``tests/test_docs_budget.py`` keeps its own hard token budgets for
``CLAUDE.md`` and ``docs/STATUS.md``.

Usage::

    uv run python scripts/doc_check.py cite
    uv run python scripts/doc_check.py size
"""

from __future__ import annotations

import argparse
import functools
import re
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Size budgets in KB (1,000 bytes), the "Target" column of the docs-pass scope (W1, W2).
SIZE_BUDGETS_KB: Mapping[str, float] = {
    "CLAUDE.md": 3.5,
    "docs/STATUS.md": 3.0,
    ".claude/agents/backtest-runner.md": 5.0,
    ".claude/agents/quant-auditor.md": 4.0,
    ".claude/agents/safety-reviewer.md": 4.0,
    ".claude/agents/implementer.md": 3.0,
    ".claude/agents/spec-critic.md": 2.5,
    ".claude/agents/doc-keeper.md": 2.0,
    ".claude/agents/researcher.md": 2.0,
    ".claude/skills/ready-pr/SKILL.md": 3.0,
    "docs/ways-of-working/teams.md": 12.0,
    "docs/ways-of-working/git-workflow.md": 10.0,
    "docs/ways-of-working/agents.md": 8.0,
    "docs/ways-of-working/development-process.md": 6.0,
    "docs/ways-of-working/research-program.md": 6.0,
}

OPEN_TASK_RE = re.compile(r"^- \[ \] ")
TASK_DEF_RE = re.compile(r"^- \[[ x]\] \*\*(T\d+[a-z]?)[ :(*]")
TASK_REF_RE = re.compile(r"\bT[1-9]\d*[a-z]?\b")
ADR_REF_RE = re.compile(r"\bADR[ -]?(\d{1,4})\b")
ADR_FILE_RE = re.compile(r"^(\d{4})-")
REQ_REF_RE = re.compile(r"\breq (\d+)\b")
REQ_ITEM_RE = re.compile(r"^(\d+)\. ")
SPEC_NAME_RE = re.compile(r"(?:specs/([a-z0-9-]+)\.md|\b([a-z0-9-]+) spec\b)", re.IGNORECASE)
#: How close (characters) a spec name must end before ``req N`` to scope it.
SPEC_SCOPE_CHARS = 30
IGNORE_MARK = "doc-check: ignore"
IGNORE_FILE_MARK = "doc-check: ignore-file"


@dataclass(frozen=True)
class Finding:
    """One unresolved citation."""

    path: str
    line: int
    kind: str
    ref: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind} {self.ref}"


@dataclass(frozen=True)
class Registry:
    """The IDs that exist: task ids, ADR numbers, claim ids and each spec's requirements."""

    tasks: frozenset[str]
    adrs: frozenset[int]
    claims: frozenset[str]
    claim_prefixes: frozenset[str]
    reqs: Mapping[str, frozenset[int]]

    @property
    def claim_ref_re(self) -> re.Pattern[str] | None:
        """A pattern for ``<PREFIX>-<n>`` with a known claim prefix, or None without claims."""
        if not self.claim_prefixes:
            return None
        return _claim_pattern(self.claim_prefixes)


@functools.cache
def _claim_pattern(prefixes: frozenset[str]) -> re.Pattern[str]:
    alternatives = "|".join(sorted(map(re.escape, prefixes)))
    return re.compile(rf"\b(?:{alternatives})-(?:[A-Z]{{0,2}}\d+|[A-Z])\b")


def plan_tasks(text: str) -> set[str]:
    """Task ids defined by a plan's task lines, open or finished."""
    return {m.group(1) for line in text.splitlines() if (m := TASK_DEF_RE.match(line))}


def spec_requirements(text: str) -> frozenset[int]:
    """The numbers of the top-level numbered items under a spec's ``## Requirements``."""
    numbers: set[int] = set()
    in_reqs = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_reqs = line.strip() == "## Requirements"
            continue
        if in_reqs and (m := REQ_ITEM_RE.match(line)):
            numbers.add(int(m.group(1)))
    return frozenset(numbers)


def claim_ids(text: str) -> set[str]:
    """Claim ids (``<PREFIX>-<n>``) in a claims.toml text."""
    data = tomllib.loads(text)
    return {str(entry["id"]) for entry in data.get("claim", [])}


def load_registry(root: Path) -> Registry:
    """Read every ID source under ``root``."""
    tasks: set[str] = set()
    for plan in sorted((root / "docs" / "plans").glob("*.md")):
        tasks |= plan_tasks(plan.read_text(encoding="utf-8"))
    adrs = {
        int(m.group(1))
        for p in (root / "docs" / "decisions").glob("*.md")
        if (m := ADR_FILE_RE.match(p.name))
    }
    claims_path = root / "docs" / "research" / "claims.toml"
    claims = claim_ids(claims_path.read_text(encoding="utf-8")) if claims_path.exists() else set()
    reqs = {
        p.stem: spec_requirements(p.read_text(encoding="utf-8"))
        for p in sorted((root / "docs" / "specs").glob("*.md"))
    }
    return Registry(
        tasks=frozenset(tasks),
        adrs=frozenset(adrs),
        claims=frozenset(claims),
        claim_prefixes=frozenset(c.rsplit("-", 1)[0] for c in claims),
        reqs=reqs,
    )


def _spec_before(line: str, pos: int, specs: Iterable[str]) -> str | None:
    """The known spec named on ``line`` just before ``pos`` (ending within
    ``SPEC_SCOPE_CHARS``), if any; a spec named earlier in another clause does not count."""
    known = set(specs)
    named = None
    for m in SPEC_NAME_RE.finditer(line, 0, pos):
        name = (m.group(1) or m.group(2)).lower()
        if name in known and pos - m.end() <= SPEC_SCOPE_CHARS:
            named = name
    return named


def check_line(
    line: str, path: str, lineno: int, registry: Registry, default_spec: str | None = None
) -> list[Finding]:
    """Every unresolved citation on one line (none on a line marked ``doc-check: ignore``)."""
    found: list[Finding] = []
    if IGNORE_MARK in line:
        return found
    for m in TASK_REF_RE.finditer(line):
        if m.group(0) not in registry.tasks:
            found.append(Finding(path, lineno, "task", m.group(0)))
    for m in ADR_REF_RE.finditer(line):
        if int(m.group(1)) not in registry.adrs:
            found.append(Finding(path, lineno, "ADR", f"{int(m.group(1)):04d}"))
    if (claim_re := registry.claim_ref_re) is not None:
        for m in claim_re.finditer(line):
            if m.group(0) not in registry.claims:
                found.append(Finding(path, lineno, "claim", m.group(0)))
    for m in REQ_REF_RE.finditer(line):
        number = int(m.group(1))
        spec = _spec_before(line, m.start(), registry.reqs) or (
            default_spec if default_spec in registry.reqs else None
        )
        if spec is not None:
            ok = number in registry.reqs[spec]
            ref = f"{spec} req {number}"
        else:
            ok = any(number in numbers for numbers in registry.reqs.values())
            ref = f"req {number}"
        if not ok:
            found.append(Finding(path, lineno, "requirement", ref))
    return found


def check_citations(root: Path) -> list[Finding]:
    """Unresolved citations in open plan task lines and in ``src/`` and ``tests/``."""
    registry = load_registry(root)
    findings: list[Finding] = []
    for plan in sorted((root / "docs" / "plans").glob("*.md")):
        rel = plan.relative_to(root).as_posix()
        for lineno, line in enumerate(plan.read_text(encoding="utf-8").splitlines(), start=1):
            if OPEN_TASK_RE.match(line):
                findings += check_line(line, rel, lineno, registry, default_spec=plan.stem)
    for top in ("src", "tests"):
        for source in sorted((root / top).rglob("*.py")):
            rel = source.relative_to(root).as_posix()
            text = source.read_text(encoding="utf-8")
            if IGNORE_FILE_MARK in text:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                findings += check_line(line, rel, lineno, registry)
    return findings


def size_warnings(root: Path, budgets: Mapping[str, float] = SIZE_BUDGETS_KB) -> list[str]:
    """A ``WARN`` line for each budgeted file over its budget (missing files are skipped)."""
    warnings = []
    for rel, budget in budgets.items():
        path = root / rel
        if not path.exists():
            continue
        kb = path.stat().st_size / 1000
        if kb > budget:
            warnings.append(f"WARN {rel}: {kb:.1f} KB, over its {budget:.1f} KB budget")
    return warnings


def main(argv: Sequence[str] | None = None) -> int:
    """Run one check; see the module docstring."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("check", choices=("cite", "size"))
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    args = parser.parse_args(argv)
    if args.check == "size":
        for warning in size_warnings(args.root):
            print(warning)
        return 0
    findings = check_citations(args.root)
    for finding in findings:
        print(finding)
    if findings:
        print(f"{len(findings)} unresolved citation(s)", file=sys.stderr)
        return 1
    print("every citation resolves")
    return 0


if __name__ == "__main__":
    sys.exit(main())

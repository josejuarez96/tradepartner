#!/usr/bin/env python3
"""Owner cockpit: one self-contained HTML page showing teams, activity, tokens and progress.

Sources, all read-only:
- GitHub via ``gh``: open issues (claims), open and merged PRs, CI state, team labels.
- The plan on ``origin/main`` through ``scripts/team.py`` (tasks, readiness, chains).
- ``docs/roadmap.md`` (phases, exit criteria, MVP range) and ``docs/STATUS.md`` (current
  phase, last tag, decisions needed from the owner).
- ``docs/research/*.md`` report headers, ``docs/decisions/*.md`` ADR headers, ``docs/specs``.
- ``docs/work-map.toml``: curated plain-English ``what`` / ``why`` per piece of work and the
  cross-cutting ``unblocks`` links. Together these become the work map: a graph of phases,
  plan tasks, issues, research, decisions and their edges, plus per-phase distance to the MVP.
- Local Claude Code session logs under ``~/.claude/projects/`` for per-team token usage,
  models and last activity. Usage is de-duplicated per API request, because one response
  is logged once per content block. Only counts and team names leave the logs.

Usage::

    uv run python scripts/cockpit.py                # writes data/cockpit/cockpit.html + .json
    uv run python scripts/cockpit.py --active-minutes 10 --idle-minutes 60 --out data/cockpit
    uv run python scripts/cockpit.py --loop 60     # regenerate every 60 s; the page reloads itself
    uv run python scripts/cockpit.py --docs-root . # read docs/ (work map etc.) from this checkout
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
import tomllib
from collections import Counter
from collections.abc import Callable, Container, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _load_team_module() -> Any:
    spec = importlib.util.spec_from_file_location("team", HERE / "team.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("team", module)
    spec.loader.exec_module(module)
    return module


team = _load_team_module()

ROADMAP_ROW_RE = re.compile(
    r"^\| \*\*(\d+): ([^*]+)\*\*(?: \*\(([^)]*)\)\*)? \| ([^|]+) \| ([^|]*)\|"
)
MVP_RANGE_RE = re.compile(r"^## MVP scope \(what Phases (\d+)\D(\d+) build\)", re.M)
ADR_LINK_RE = re.compile(r"\]\([^)]*decisions/(\d{4})-[\w-]+\.md\)")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z*`(])")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
H1_RE = re.compile(r"^# (.+)$", re.M)
RESEARCH_HEADER_RE = re.compile(
    r"\*\*Brief:\*\*\s*(?P<brief>.*?)\s*·\s*\*\*Date:\*\*\s*(?P<date>\S+)\s*·\s*"
    r"\*\*Status:\*\*\s*(?P<status>[A-Z]+)"
)
ADR_TITLE_RE = re.compile(r"^# (\d{4})\. (.+)$", re.M)
DOC_STATUS_RE = re.compile(r"\*\*Status:\*\*\s*(\w+)")
DOC_ISSUE_RE = re.compile(r"\*\*Issue:\*\*\s*#(\d+)")
DOC_PHASE_RE = re.compile(r"\(Phase (\d+)\)")
TITLE_PHASE_RE = re.compile(r"\bPhase (\d+)\b")
WORK_MAP_KINDS = ("phase", "task", "issue", "research", "adr", "plan", "spec")
STATE_ORDER = ("done", "in_review", "in_progress", "ready", "blocked", "open")
STATUS_HEADER_RE = re.compile(
    r"\*\*Phase:\*\* (?P<phase>\d+), (?P<name>[^·]+?) · \*\*Last tag:\*\* (?P<tag>\S+)"
)
CHAIN_ROW_RE = re.compile(r"^\| ([a-z][a-z0-9-]*) \| ([^|]+) \| ([^|]+) \|")
TEAM_CMD_RE = re.compile(r"team\.py (?:start|register) ([a-z][a-z0-9-]{0,19})\b")
USAGE_KEYS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
)


# ── pure parsing ────────────────────────────────────────────────────────────────


def strip_markdown(text: str) -> str:
    """Links become their text; bold and italic markers go; backticks stay."""
    return MD_LINK_RE.sub(r"\1", text).replace("**", "").replace("*", "").strip()


def split_criteria(cell: str) -> list[dict[str, Any]]:
    """One roadmap exit-criteria cell into sentences, each with the ADRs it links to."""
    out = []
    for raw in SENTENCE_SPLIT_RE.split(cell.strip()):
        raw = raw.strip()
        if not raw:
            continue
        adrs = sorted(set(ADR_LINK_RE.findall(raw)))
        out.append({"text": strip_markdown(raw), "adrs": adrs})
    return out


def parse_roadmap(text: str) -> list[dict[str, Any]]:
    """Phase rows from docs/roadmap.md: number, name, note, goal and exit criteria."""
    phases = []
    for line in text.splitlines():
        m = ROADMAP_ROW_RE.match(line)
        if m:
            phases.append(
                {
                    "phase": int(m.group(1)),
                    "name": m.group(2).strip(),
                    "note": (m.group(3) or "").strip(),
                    "goal": m.group(4).strip(),
                    "criteria": split_criteria(m.group(5)),
                }
            )
    return phases


def parse_mvp(text: str) -> dict[str, Any]:
    """The MVP phase range and its first paragraph, from the roadmap's "MVP scope" section."""
    m = MVP_RANGE_RE.search(text)
    if not m:
        return {"first_phase": None, "last_phase": None, "summary": ""}
    rest = text[m.end() :].strip().split("\n\n", 1)[0]
    return {
        "first_phase": int(m.group(1)),
        "last_phase": int(m.group(2)),
        "summary": strip_markdown(" ".join(rest.split())),
    }


def parse_status_decisions(text: str) -> list[str]:
    """Bullets under STATUS's "Decisions needed from owner", minus the "none" placeholders."""
    out: list[str] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_section = line.lower().startswith("## decisions needed")
            continue
        if in_section and line.startswith("- "):
            item = line[2:].strip()
            if not item.lower().startswith("none"):
                out.append(strip_markdown(item))
    return out


def parse_research_report(text: str, stem: str) -> dict[str, Any]:
    """Title, status and brief of one docs/research file; status None when it has no header."""
    h1 = H1_RE.search(text)
    title = re.sub(r"^Research Report:\s*", "", h1.group(1).strip()) if h1 else stem
    m = RESEARCH_HEADER_RE.search(text)
    if not m:
        return {"id": stem, "title": title, "status": None, "date": None, "brief_issue": None}
    issue = re.search(r"#(\d+)", m.group("brief"))
    return {
        "id": stem,
        "title": title,
        "status": m.group("status"),
        "date": m.group("date"),
        "brief_issue": int(issue.group(1)) if issue else None,
    }


def parse_doc_header(text: str, stem: str) -> dict[str, Any]:
    """Number (ADRs), title, status, issue and phase of an ADR, spec or plan file."""
    adr = ADR_TITLE_RE.search(text)
    h1 = H1_RE.search(text)
    title = adr.group(2).strip() if adr else (h1.group(1).strip() if h1 else stem)
    title = re.sub(r"^(Spec|Plan):\s*", "", title)
    status = DOC_STATUS_RE.search(text)
    issue = DOC_ISSUE_RE.search(text)
    phase = DOC_PHASE_RE.search(title)
    return {
        "id": adr.group(1) if adr else stem,
        "title": DOC_PHASE_RE.sub("", title).strip(),
        "status": status.group(1) if status else None,
        "issue": int(issue.group(1)) if issue else None,
        "phase": int(phase.group(1)) if phase else None,
    }


def load_work_map(text: str) -> dict[str, dict[str, Any]]:
    """docs/work-map.toml as ``{"kind:key": {what, why, unblocks}}``; bad kinds raise."""
    out: dict[str, dict[str, Any]] = {}
    for kind, entries in tomllib.loads(text).items():
        if kind not in WORK_MAP_KINDS:
            raise ValueError(f"work map: unknown kind {kind!r}, expected one of {WORK_MAP_KINDS}")
        for key, entry in entries.items():
            unblocks = [str(u) for u in entry.get("unblocks", [])]
            for u in unblocks:
                if u.split(":", 1)[0] not in WORK_MAP_KINDS or ":" not in u:
                    raise ValueError(f"work map: {kind}.{key} unblocks {u!r} is not kind:key")
            out[f"{kind}:{key}"] = {
                "what": str(entry.get("what", "")).strip(),
                "why": str(entry.get("why", "")).strip(),
                "unblocks": unblocks,
            }
    return out


def parse_status_header(text: str) -> dict[str, Any]:
    m = STATUS_HEADER_RE.search(text)
    if not m:
        return {"phase": None, "phase_name": "", "last_tag": ""}
    return {
        "phase": int(m.group("phase")),
        "phase_name": m.group("name").strip(),
        "last_tag": m.group("tag"),
    }


def parse_chains(text: str) -> list[dict[str, Any]]:
    """Rows of a plan's Chains table that name at least one task id."""
    chains = []
    in_chains = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_chains = line.lower().startswith("## chains")
            continue
        if not in_chains:
            continue
        m = CHAIN_ROW_RE.match(line)
        if not m:
            continue
        ids = team.TASK_REF_RE.findall(m.group(2))
        if not ids:
            continue
        chains.append({"name": m.group(1), "tasks": ids, "starts": m.group(3).strip()})
    return chains


def task_state(task: Any, by_id: dict[str, Any], holders: dict[str, str]) -> str:
    """done · in_progress · ready · blocked, with owner tasks flagged separately."""
    if task.done:
        return "done"
    if task.id in holders:
        return "in_progress"
    if team.is_ready(task, by_id):
        return "ready"
    return "blocked"


def phase_state(phase: int, current: int | None) -> str:
    """done · current · future relative to STATUS's current phase."""
    if current is None:
        return "future"
    return "done" if phase < current else "current" if phase == current else "future"


def in_mvp(phase: int, mvp: dict[str, Any]) -> bool:
    first, last = mvp.get("first_phase"), mvp.get("last_phase")
    return first is not None and last is not None and first <= phase <= last


def issue_kind(labels: Iterable[str]) -> str:
    """research · decision · docs · issue, from the type label."""
    for lb in labels:
        if lb == "type:research":
            return "research"
        if lb == "type:decision":
            return "decision"
        if lb == "type:docs":
            return "docs"
    return "issue"


def build_graph(
    *,
    roadmap: list[dict[str, Any]],
    current_phase: int | None,
    mvp: dict[str, Any],
    tasks: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    prs: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    adrs: list[dict[str, Any]],
    docs: list[dict[str, Any]],
    work_map: dict[str, dict[str, Any]],
    repo_url: str = "",
) -> dict[str, Any]:
    """The work map: nodes (phases, tasks, issues, research, ADRs, specs, plans) and edges.

    A plan task folds in its canonical issue and open PRs; a research report folds into its
    brief issue while that issue is open. Edges: plan ``depends_on``; work-map ``unblocks``;
    ADRs named in a phase's exit criteria; a plan's sink tasks, spec and plan to their phase;
    phase to next phase (an optional phase is bypassed). Returns the ids with no map entry.
    """
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    prs_by_issue: dict[int, list[dict[str, Any]]] = {}
    for p in prs:
        if p.get("issue") is not None:
            prs_by_issue.setdefault(p["issue"], []).append(p)

    def add(node_id: str, **fields: Any) -> dict[str, Any]:
        entry = work_map.get(node_id, {})
        node = {
            "id": node_id,
            "kind": node_id.split(":", 1)[0],
            "what": entry.get("what", ""),
            "why": entry.get("why", ""),
            "holder": None,
            "issue": None,
            "prs": [],
            "url": "",
            **fields,
        }
        nodes[node_id] = node
        return node

    def edge(src: str, dst: str, kind: str) -> None:
        edges.append({"from": src, "to": dst, "kind": kind})

    def pr_state(issue_no: int | None, fallback: str) -> str:
        return "in_review" if issue_no is not None and prs_by_issue.get(issue_no) else fallback

    for p in roadmap:
        add(
            f"phase:{p['phase']}",
            label=f"Phase {p['phase']}",
            title=p["name"],
            state=phase_state(p["phase"], current_phase),
            note=p["note"],
            mvp=in_mvp(p["phase"], mvp),
        )
        for c in p["criteria"]:
            for a in c["adrs"]:
                edge(f"adr:{a}", f"phase:{p['phase']}", "gate")
    last_required: str | None = None
    for p in roadmap:
        pid = f"phase:{p['phase']}"
        if last_required:
            edge(last_required, pid, "phase")
        if not p["note"]:
            last_required = pid

    issue_of_task: dict[str, int] = {}
    for i in issues:
        for tid in team.tasks_of(i["labels"]):
            issue_of_task[tid] = min(issue_of_task.get(tid, i["number"]), i["number"])
    for t in tasks:
        issue_no = issue_of_task.get(t["id"])
        state = t["state"] if t["state"] == "done" else pr_state(issue_no, t["state"])
        add(
            f"task:{t['id']}",
            label=t["id"],
            title=t["title"],
            state=state,
            owner=t["owner"],
            holder=t["holder"],
            issue=issue_no,
            prs=prs_by_issue.get(issue_no, []) if issue_no is not None else [],
            phase=t["phase"],
            url=f"{repo_url}/issues/{issue_no}" if issue_no and repo_url else "",
        )
        for dep in t["depends_on"]:
            edge(f"task:{dep}", f"task:{t['id']}", "depends")
    has_dependent = {d["from"] for d in edges if d["kind"] == "depends"}
    for t in tasks:
        if f"task:{t['id']}" not in has_dependent and t["phase"] is not None:
            edge(f"task:{t['id']}", f"phase:{t['phase']}", "gate")

    folded_tasks = {n for n in issue_of_task.values()}
    open_by_number = {i["number"]: i for i in issues}
    reports_by_issue = {r["brief_issue"]: r for r in reports if r["brief_issue"] in open_by_number}
    for i in issues:
        if i["number"] in folded_tasks:
            continue
        holder = team.team_of(i["labels"])
        report = reports_by_issue.get(i["number"])
        add(
            f"issue:{i['number']}",
            kind=issue_kind(i["labels"]),
            label=f"#{i['number']}",
            title=i["title"],
            state=pr_state(i["number"], "in_progress" if holder else "open"),
            holder=holder,
            issue=i["number"],
            prs=prs_by_issue.get(i["number"], []),
            labels=[lb for lb in i["labels"] if lb.startswith(("type:", "size:", "phase:"))],
            report_status=report["status"] if report else None,
            url=f"{repo_url}/issues/{i['number']}" if repo_url else "",
        )
    for r in reports:
        if r["brief_issue"] in open_by_number:
            continue
        add(
            f"research:{r['id']}",
            label="report",
            title=r["title"],
            state="done",
            report_status=r["status"],
            issue=r["brief_issue"],
            url=f"{repo_url}/blob/main/docs/research/{r['id']}.md" if repo_url else "",
        )
    for a in adrs:
        add(
            f"adr:{a['id']}",
            label=f"ADR {a['id']}",
            title=a["title"],
            state="done" if a["status"] == "Accepted" else "in_review",
            doc_status=a["status"],
            issue=a["issue"],
            url=f"{repo_url}/blob/main/docs/decisions/{a['file']}" if repo_url else "",
        )
    for d in docs:
        node_id = f"{d['kind']}:{d['id']}"
        add(
            node_id,
            label=d["kind"],
            title=d["title"],
            state="done",
            doc_status=d["status"],
            issue=d["issue"],
            phase=d["phase"],
            url=f"{repo_url}/blob/main/docs/{d['kind']}s/{d['id']}.md" if repo_url else "",
        )
        if d["phase"] is not None:
            edge(node_id, f"phase:{d['phase']}", "gate")

    for node_id, entry in work_map.items():
        for target in entry["unblocks"]:
            if node_id in nodes and target in nodes:
                edge(node_id, target, "unblocks")
    seen: set[tuple[str, str]] = set()
    kept = []
    for e in edges:
        key = (e["from"], e["to"])
        if e["from"] in nodes and e["to"] in nodes and key not in seen:
            seen.add(key)
            kept.append(e)
    missing = sorted(n for n in nodes if n not in work_map)
    return {"nodes": list(nodes.values()), "edges": kept, "missing_map_entries": missing}


def phase_progress(
    roadmap: list[dict[str, Any]],
    current_phase: int | None,
    mvp: dict[str, Any],
    graph_nodes: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    adrs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Per phase: task counts by state, the spec issue when unplanned, exit criteria states."""
    accepted = {a["id"] for a in adrs if a["status"] == "Accepted"}
    out = []
    for p in roadmap:
        n = p["phase"]
        counts = dict.fromkeys(STATE_ORDER, 0)
        for node in graph_nodes:
            if node["kind"] == "task" and node.get("phase") == n:
                counts[node["state"]] = counts.get(node["state"], 0) + 1
        total = sum(counts.values())
        spec_issue = next(
            (
                i
                for i in issues
                if issue_kind(i["labels"]) == "docs"
                and (m := TITLE_PHASE_RE.search(i["title"]))
                and int(m.group(1)) == n
            ),
            None,
        )
        state = phase_state(n, current_phase)
        criteria = []
        for c in p["criteria"]:
            if state == "done" or (c["adrs"] and all(a in accepted for a in c["adrs"])):
                cstate = "done"
            elif c["adrs"]:
                cstate = "missing_adr"
            else:
                cstate = "needs_owner"
            criteria.append({**c, "state": cstate})
        out.append(
            {
                "phase": n,
                "name": p["name"],
                "note": p["note"],
                "goal": strip_markdown(p["goal"]),
                "state": state,
                "mvp": in_mvp(n, mvp),
                "planned": total > 0,
                "counts": counts,
                "total": total,
                "spec_issue": (
                    {
                        "number": spec_issue["number"],
                        "title": spec_issue["title"],
                        "holder": team.team_of(spec_issue["labels"]),
                    }
                    if spec_issue
                    else None
                ),
                "criteria": criteria,
            }
        )
    return out


# ── session logs ────────────────────────────────────────────────────────────────


@dataclass
class SessionSummary:
    team: str | None = None
    requests: int = 0
    tokens: dict[str, int] = field(default_factory=lambda: dict.fromkeys(USAGE_KEYS, 0))
    models: set[str] = field(default_factory=set)
    first_seen: str | None = None
    last_seen: str | None = None
    cwds: set[str] = field(default_factory=set)


def summarize_session(
    records: Iterable[dict[str, Any]],
    in_scope: Callable[[str], bool],
    team_of_cwd: Callable[[str], str | None],
    main_team: str | None = None,
    known_teams: Container[str] | None = None,
) -> SessionSummary | None:
    """Aggregate one session's log. Returns None when no record is inside the project.

    Team attribution, in order: the team directory the session worked in most (the harness
    sometimes resets a session's cwd to the main checkout, so "last cwd" is unreliable); a
    ``start``/``register`` command naming a team whose directory still exists (a deleted
    probe does not count); the main checkout's own team.
    """
    out = SessionSummary()
    usage_by_request: dict[str, dict[str, int]] = {}
    cmd_team: str | None = None
    cwd_counts: Counter[str] = Counter()
    for r in records:
        cwd = str(r.get("cwd", ""))
        if not cwd or not in_scope(cwd):
            continue
        out.cwds.add(cwd)
        cwd_counts[cwd] += 1
        ts = r.get("timestamp")
        if isinstance(ts, str):
            out.first_seen = ts if out.first_seen is None or ts < out.first_seen else out.first_seen
            out.last_seen = ts if out.last_seen is None or ts > out.last_seen else out.last_seen
        msg = r.get("message")
        if r.get("type") != "assistant" or not isinstance(msg, dict):
            continue
        if isinstance(msg.get("model"), str):
            out.models.add(msg["model"])
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                cmd = str((block.get("input") or {}).get("command", ""))
                for m in TEAM_CMD_RE.finditer(cmd):
                    cmd_team = m.group(1)
        usage = msg.get("usage")
        if isinstance(usage, dict):
            key = str(r.get("requestId") or r.get("uuid"))
            counts = {k: int(usage.get(k) or 0) for k in USAGE_KEYS}
            prev = usage_by_request.get(key)
            if prev is None or counts["output_tokens"] >= prev["output_tokens"]:
                usage_by_request[key] = counts
    if not out.cwds:
        return None
    out.requests = len(usage_by_request)
    for counts in usage_by_request.values():
        for k in USAGE_KEYS:
            out.tokens[k] += counts[k]
    for c, _ in cwd_counts.most_common():
        t = team_of_cwd(c)
        if t and t != main_team:
            out.team = t
            break
    if out.team is None and cmd_team and (known_teams is None or cmd_team in known_teams):
        out.team = cmd_team
    if out.team is None:
        out.team = next((t for t in (team_of_cwd(c) for c in sorted(out.cwds)) if t), None)
    return out


def activity_state(last_seen: str | None, now: datetime, active_min: int, idle_min: int) -> str:
    if not last_seen:
        return "inactive"
    seen = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
    age = now - seen
    if age <= timedelta(minutes=active_min):
        return "active"
    if age <= timedelta(minutes=idle_min):
        return "idle"
    return "inactive"


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict):
                yield r


# ── collection ──────────────────────────────────────────────────────────────────


class GhExtra(team.GhCli):
    """The few queries the cockpit needs beyond team.py's."""

    def open_issues(self) -> list[dict[str, Any]]:
        raw = self._run(
            "issue",
            "list",
            "--state",
            "open",
            "--limit",
            "200",
            "--json",
            "number,title,labels,updatedAt",
        )
        return [
            {
                "number": i["number"],
                "title": i["title"],
                "updated": i["updatedAt"],
                "labels": [lb["name"] for lb in i["labels"]],
            }
            for i in json.loads(raw)
        ]

    def open_prs(self) -> list[dict[str, Any]]:
        raw = self._run(
            "pr",
            "list",
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "number,title,headRefName,isDraft,labels,updatedAt,statusCheckRollup",
        )
        out = []
        for p in json.loads(raw):
            checks = [
                c.get("conclusion") or c.get("state") or ""
                for c in p.get("statusCheckRollup") or []
            ]
            ci = (
                "pass"
                if checks and all(c == "SUCCESS" for c in checks)
                else ("fail" if any(c in ("FAILURE", "ERROR") for c in checks) else "pending")
            )
            out.append(
                {
                    "number": p["number"],
                    "title": p["title"],
                    "branch": p["headRefName"],
                    "draft": p["isDraft"],
                    "updated": p["updatedAt"],
                    "ci": ci,
                    "labels": [lb["name"] for lb in p["labels"]],
                    "issue": team.issue_number_from_branch(p["headRefName"]),
                }
            )
        return out

    def merged_prs(self, limit: int = 12) -> list[dict[str, Any]]:
        raw = self._run(
            "pr",
            "list",
            "--state",
            "merged",
            "--limit",
            str(limit),
            "--json",
            "number,title,mergedAt,headRefName",
        )
        return [
            {
                "number": p["number"],
                "title": p["title"],
                "merged": p["mergedAt"],
                "issue": team.issue_number_from_branch(p["headRefName"]),
            }
            for p in json.loads(raw)
        ]

    def repo_url(self) -> str:
        return str(json.loads(self._run("repo", "view", "--json", "url"))["url"])

    def team_labels(self) -> list[str]:
        raw = self._run("label", "list", "--limit", "200", "--json", "name")
        return sorted(
            lb["name"].removeprefix(team.TEAM_LABEL_PREFIX)
            for lb in json.loads(raw)
            if lb["name"].startswith(team.TEAM_LABEL_PREFIX)
        )


def team_dirs(main: Path) -> dict[str, str]:
    """team name -> directory, from every .team file we can find."""
    found: dict[str, str] = {}
    candidates = [
        main,
        *sorted((main / ".claude" / "worktrees").glob("*")),
        *sorted(team.teams_dir(main).glob("*")),
    ]
    for d in candidates:
        f = d / team.TEAM_FILE
        if f.is_file():
            found.setdefault(f.read_text().strip(), str(d))
    return found


def collect(
    gh: GhExtra,
    main: Path,
    projects_dir: Path,
    now: datetime,
    active_min: int,
    idle_min: int,
    ref: str | None = team.DEFAULT_PLAN_REF,
    since: str | None = None,
    docs_root: Path | None = None,
) -> dict[str, Any]:
    """Everything the page shows. ``docs_root`` (default ``main``) is the checkout whose
    ``docs/`` are read; plans still come from ``ref``."""
    docs_dir = (docs_root or main) / "docs"
    status_text = docs_dir.joinpath("STATUS.md").read_text()
    roadmap_text = docs_dir.joinpath("roadmap.md").read_text()
    roadmap = parse_roadmap(roadmap_text)
    mvp = parse_mvp(roadmap_text)
    header = parse_status_header(status_text)
    reports = [
        parse_research_report(p.read_text(), p.stem)
        for p in sorted((docs_dir / "research").glob("*.md"))
    ]
    adrs = [
        {**parse_doc_header(p.read_text(), p.stem), "file": p.name}
        for p in sorted((docs_dir / "decisions").glob("[0-9]*.md"))
    ]
    docs = [
        {**parse_doc_header(p.read_text(), p.stem), "kind": kind}
        for kind in ("spec", "plan")
        for p in sorted((docs_dir / f"{kind}s").glob("*.md"))
    ]
    map_file = docs_dir / "work-map.toml"
    work_map = load_work_map(map_file.read_text()) if map_file.is_file() else {}
    repo_url = gh.repo_url()
    plans = team.read_plans(main, ref)
    tasks = [
        t for text, name in ((v, k) for k, v in plans.items()) for t in team.parse_plan(text, name)
    ]
    by_id = {t.id: t for t in tasks}
    chains = [c for text in plans.values() for c in parse_chains(text)]

    issues = gh.open_issues()
    prs = gh.open_prs()
    merged = gh.merged_prs()
    holders: dict[str, str] = {}
    claims_by_team: dict[str, list[dict[str, Any]]] = {}
    for i in issues:
        t = team.team_of(i["labels"])
        if not t:
            continue
        for tid in team.tasks_of(i["labels"]):
            holders[tid] = t
        claims_by_team.setdefault(t, []).append(
            {
                "number": i["number"],
                "title": i["title"],
                "updated": i["updated"],
                "tasks": team.tasks_of(i["labels"]),
                "prs": [p for p in prs if p["issue"] == i["number"]],
            }
        )

    dirs = team_dirs(main)
    teams_root = team.teams_dir(main)

    def in_scope(cwd: str) -> bool:
        return cwd.startswith(str(main)) or cwd.startswith(str(teams_root))

    def team_of_cwd(cwd: str) -> str | None:
        p = Path(cwd)
        for d in (p, *p.parents):
            f = d / team.TEAM_FILE
            if f.is_file():
                return f.read_text().strip()
            if d in (main, teams_root):
                break
        return None

    sessions_by_team: dict[str, list[SessionSummary]] = {}
    for jsonl in sorted(projects_dir.glob("*/*.jsonl")):
        s = summarize_session(
            read_jsonl(jsonl),
            in_scope,
            team_of_cwd,
            main_team=team_of_cwd(str(main)),
            known_teams=set(dirs),
        )
        if s and since and (s.last_seen or "") < since:
            continue
        if s:
            sessions_by_team.setdefault(s.team or "unassigned", []).append(s)

    names = sorted(set(gh.team_labels()) | set(dirs) | set(sessions_by_team) | set(claims_by_team))
    teams = []
    for name in names:
        ss = sessions_by_team.get(name, [])
        tokens = dict.fromkeys(USAGE_KEYS, 0)
        for s in ss:
            for k in USAGE_KEYS:
                tokens[k] += s.tokens[k]
        last_log = max((s.last_seen for s in ss if s.last_seen), default=None)
        gh_times = [c["updated"] for c in claims_by_team.get(name, [])] + [
            p["updated"] for c in claims_by_team.get(name, []) for p in c["prs"]
        ]
        last = max([t for t in (last_log, *gh_times) if t], default=None)
        teams.append(
            {
                "name": name,
                "dir": dirs.get(name),
                "owner_session": dirs.get(name) == str(main),
                "state": activity_state(last, now, active_min, idle_min),
                "last_seen": last,
                "last_log": last_log,
                "sessions": len(ss),
                "requests": sum(s.requests for s in ss),
                "tokens": tokens,
                "tokens_total": sum(tokens.values()),
                "models": sorted({m for s in ss for m in s.models}),
                "claims": claims_by_team.get(name, []),
            }
        )

    plan_tasks = [
        {
            "id": t.id,
            "title": t.title,
            "owner": t.owner,
            "depends_on": list(t.depends_on),
            "state": task_state(t, by_id, holders),
            "holder": holders.get(t.id),
            "plan": t.plan,
            "phase": t.phase,
        }
        for t in tasks
    ]
    done = sum(1 for t in tasks if t.done)
    unclaimed = [i for i in issues if not team.team_of(i["labels"])]
    graph = build_graph(
        roadmap=roadmap,
        current_phase=header["phase"],
        mvp=mvp,
        tasks=plan_tasks,
        issues=issues,
        prs=prs,
        reports=reports,
        adrs=adrs,
        docs=docs,
        work_map=work_map,
        repo_url=repo_url,
    )
    progress = phase_progress(roadmap, header["phase"], mvp, graph["nodes"], issues, adrs)
    return {
        "generated_at": now.isoformat(),
        "thresholds": {"active_minutes": active_min, "idle_minutes": idle_min, "since": since},
        "repo_url": repo_url,
        "phase": header,
        "roadmap": roadmap,
        "mvp": mvp,
        "progress": progress,
        "graph": graph,
        "owner": {
            "decisions": parse_status_decisions(status_text),
            "tasks": [t for t in plan_tasks if t["owner"] and t["state"] != "done"],
            "ready_prs": [p for p in prs if not p["draft"]],
        },
        "plan": {"tasks": plan_tasks, "chains": chains, "done": done, "total": len(tasks)},
        "teams": teams,
        "unclaimed": unclaimed,
        "open_prs": prs,
        "merged": merged,
    }


# ── rendering ───────────────────────────────────────────────────────────────────

TEMPLATE = (HERE / "cockpit_template.html").read_text


def render(data: dict[str, Any], refresh_seconds: int = 0) -> str:
    """Embed the data; with ``refresh_seconds`` the page reloads itself at that interval."""
    payload = json.dumps(data, default=str).replace("</", "<\\/")
    meta = f'<meta http-equiv="refresh" content="{refresh_seconds}">' if refresh_seconds > 0 else ""
    return TEMPLATE().replace("__COCKPIT_DATA__", payload).replace("__COCKPIT_REFRESH__", meta)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--out", default="data/cockpit", help="output directory (gitignored by default)"
    )
    parser.add_argument("--active-minutes", type=int, default=10)
    parser.add_argument("--idle-minutes", type=int, default=60)
    parser.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument(
        "--since", default=None, help="ignore sessions last active before this ISO time"
    )
    parser.add_argument(
        "--loop", type=int, default=0, help="regenerate every N seconds until killed"
    )
    parser.add_argument(
        "--docs-root",
        default=None,
        help="checkout whose docs/ to read (default: the main clone); plans come from origin/main",
    )
    args = parser.parse_args(argv)
    main_dir = team.main_root()
    docs_root = Path(args.docs_root).resolve() if args.docs_root else None
    out = (main_dir / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    def once() -> None:
        data = collect(
            GhExtra(),
            main_dir,
            Path(args.projects_dir),
            datetime.now(UTC),
            args.active_minutes,
            args.idle_minutes,
            since=args.since,
            docs_root=docs_root,
        )
        (out / "cockpit.json").write_text(json.dumps(data, indent=2, default=str))
        (out / "cockpit.html").write_text(render(data, refresh_seconds=args.loop))
        active = sum(1 for t in data["teams"] if t["state"] == "active")
        print(
            f"{datetime.now(UTC).strftime('%H:%M:%S')} wrote {out / 'cockpit.html'}  "
            f"teams={len(data['teams'])} active={active} "
            f"plan={data['plan']['done']}/{data['plan']['total']} open_prs={len(data['open_prs'])}",
            flush=True,
        )

    if args.loop <= 0:
        once()
        return 0
    while True:  # a transient gh, git or file error must not end the loop
        try:
            once()
        except (SystemExit, Exception) as exc:  # keep the loop alive, report the round
            print(f"skipped this round: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(args.loop)


if __name__ == "__main__":
    sys.exit(main())

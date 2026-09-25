#!/usr/bin/env python3
"""Owner cockpit: one self-contained HTML page showing teams, activity, tokens and progress.

Sources, all read-only:
- GitHub via ``gh``: open issues (claims), open and merged PRs, CI state, team labels.
- The plan on ``origin/main`` through ``scripts/team.py`` (tasks, readiness, chains).
- ``docs/roadmap.md`` (phases) and ``docs/STATUS.md`` (current phase, last tag).
- Local Claude Code session logs under ``~/.claude/projects/`` for per-team token usage,
  models and last activity. Usage is de-duplicated per API request, because one response
  is logged once per content block. Only counts and team names leave the logs.

Usage::

    uv run python scripts/cockpit.py                # writes data/cockpit/cockpit.html + .json
    uv run python scripts/cockpit.py --active-minutes 10 --idle-minutes 60 --out data/cockpit
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
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

ROADMAP_ROW_RE = re.compile(r"^\| \*\*(\d+): ([^*]+)\*\* \| ([^|]+) \|")
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


def parse_roadmap(text: str) -> list[dict[str, Any]]:
    """Phase rows from docs/roadmap.md: number, name, goal."""
    phases = []
    for line in text.splitlines():
        m = ROADMAP_ROW_RE.match(line)
        if m:
            phases.append(
                {"phase": int(m.group(1)), "name": m.group(2).strip(), "goal": m.group(3).strip()}
            )
    return phases


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
) -> dict[str, Any]:
    status_text = main.joinpath("docs", "STATUS.md").read_text()
    roadmap = parse_roadmap(main.joinpath("docs", "roadmap.md").read_text())
    header = parse_status_header(status_text)
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
        }
        for t in tasks
    ]
    done = sum(1 for t in tasks if t.done)
    unclaimed = [i for i in issues if not team.team_of(i["labels"])]
    return {
        "generated_at": now.isoformat(),
        "thresholds": {"active_minutes": active_min, "idle_minutes": idle_min, "since": since},
        "phase": header,
        "roadmap": roadmap,
        "plan": {"tasks": plan_tasks, "chains": chains, "done": done, "total": len(tasks)},
        "teams": teams,
        "unclaimed": unclaimed,
        "open_prs": prs,
        "merged": merged,
    }


# ── rendering ───────────────────────────────────────────────────────────────────

TEMPLATE = (HERE / "cockpit_template.html").read_text


def render(data: dict[str, Any]) -> str:
    payload = json.dumps(data, default=str).replace("</", "<\\/")
    return TEMPLATE().replace("__COCKPIT_DATA__", payload)


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
    args = parser.parse_args(argv)
    main_dir = team.main_root()
    data = collect(
        GhExtra(),
        main_dir,
        Path(args.projects_dir),
        datetime.now(UTC),
        args.active_minutes,
        args.idle_minutes,
        since=args.since,
    )
    out = (main_dir / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "cockpit.json").write_text(json.dumps(data, indent=2, default=str))
    (out / "cockpit.html").write_text(render(data))
    active = sum(1 for t in data["teams"] if t["state"] == "active")
    print(
        f"wrote {out / 'cockpit.html'}  teams={len(data['teams'])} active={active} "
        f"plan={data['plan']['done']}/{data['plan']['total']} open_prs={len(data['open_prs'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

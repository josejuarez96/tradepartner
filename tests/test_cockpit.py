"""Tests for scripts/cockpit.py: parsing, session aggregation and state classification."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "cockpit", Path(__file__).resolve().parents[1] / "scripts" / "cockpit.py"
)
assert _SPEC is not None and _SPEC.loader is not None
cockpit = importlib.util.module_from_spec(_SPEC)
sys.modules["cockpit"] = cockpit
_SPEC.loader.exec_module(cockpit)
team = cockpit.team

ROADMAP = """| Phase | Goal | Exit criteria (draft) |
|---|---|---|
| **0: Foundations** | Repo, tooling, and ways of working | Ways of working merged |
| **2: Data foundation** | Point-in-time local store | Every fact has `known_at` |
"""

STATUS = (
    "**Updated:** 2026-09-24 · **Phase:** 2, Data foundation · "
    "**Last tag:** v0.1.0 · **Next tag:** v0.2.0"
)

PLAN = """# Plan: Data foundation (Phase 2)

## Tasks
- [x] **T4: Store.** Files: `s.py` · Depends on: n/a · Review: qa.
- [ ] **T5: Fixture universe.** Files: `f.py` · Depends on: T4 · Review: qa.
- [ ] **T6: As-of.** Files: `a.py` · Depends on: T5 · Review: qa.
- [ ] **T3 (owner): Record.** Files: `x` · Depends on: n/a · Review: sr.

## Chains (for team claims)

| Chain | Tasks | Starts when |
|---|---|---|
| universe | T5 → T6 | T4 merged |
| fixes | open `size:S` issues | any time |

## Verification
"""


def test_parse_roadmap_and_status_header() -> None:
    phases = cockpit.parse_roadmap(ROADMAP)
    assert [p["phase"] for p in phases] == [0, 2]
    assert phases[1]["name"] == "Data foundation" and "local store" in phases[1]["goal"]
    header = cockpit.parse_status_header(STATUS)
    assert header == {"phase": 2, "phase_name": "Data foundation", "last_tag": "v0.1.0"}
    assert cockpit.parse_status_header("nothing here")["phase"] is None


def test_parse_chains_keeps_only_rows_with_task_ids() -> None:
    chains = cockpit.parse_chains(PLAN)
    assert chains == [{"name": "universe", "tasks": ["T5", "T6"], "starts": "T4 merged"}]


def test_task_state_by_plan_and_holders() -> None:
    tasks = team.parse_plan(PLAN, "p")
    by_id = {t.id: t for t in tasks}
    holders = {"T5": "lyon"}
    states = {t.id: cockpit.task_state(t, by_id, holders) for t in tasks}
    assert states == {"T4": "done", "T5": "in_progress", "T6": "blocked", "T3": "ready"}


def _rec(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "cwd": "/repo",
        "timestamp": "2026-09-25T01:00:00.000Z",
        "type": "assistant",
    }
    base.update(kw)
    return base


def _assistant(
    request: str, out_tokens: int, cmd: str | None = None, model: str = "claude-opus-5-5"
) -> dict[str, object]:
    content: list[dict[str, object]] = []
    if cmd:
        content.append({"type": "tool_use", "name": "Bash", "input": {"command": cmd}})
    return _rec(
        requestId=request,
        message={
            "model": model,
            "content": content,
            "usage": {
                "input_tokens": 10,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 5,
                "output_tokens": out_tokens,
            },
        },
    )


def test_summarize_session_dedupes_per_request_and_names_team_from_command() -> None:
    records = [
        _rec(type="user", timestamp="2026-09-25T00:59:00.000Z"),
        _assistant("req-1", 50, cmd="uv run python scripts/team.py start Centurion || true"),
        _assistant("req-1", 50),  # second content block of the same response: identical usage
        _assistant("req-2", 7, cmd="uv run python scripts/team.py register centurion"),
        _rec(type="assistant", timestamp="2026-09-25T01:05:00.000Z", message={"content": []}),
        _rec(cwd="/elsewhere", requestId="req-9", message={"usage": {"output_tokens": 999}}),
    ]
    s = cockpit.summarize_session(records, lambda c: c.startswith("/repo"), lambda c: None)
    assert s is not None
    assert s.requests == 2
    assert s.tokens == {
        "input_tokens": 20,
        "cache_read_input_tokens": 200,
        "cache_creation_input_tokens": 10,
        "output_tokens": 57,
    }
    assert s.team == "centurion"  # no team directory seen, so the last start/register command wins
    assert s.models == {"claude-opus-5-5"}
    assert (s.first_seen, s.last_seen) == ("2026-09-25T00:59:00.000Z", "2026-09-25T01:05:00.000Z")


def test_summarize_session_attribution_order() -> None:
    def by_cwd(c: str) -> str | None:
        return {"/repo": "atlas", "/teams/lyon": "lyon"}.get(c)

    records = [_assistant("r", 1)]
    s = cockpit.summarize_session(records, lambda c: True, by_cwd)
    assert s is not None and s.team == "atlas"
    # The team directory the session worked in wins over the main checkout, however often
    # the harness reset the cwd back to main.
    records = [_assistant("r1", 1), _assistant("r2", 1), _assistant("r3", 1, cmd="x")]
    records[1]["cwd"] = "/teams/lyon"
    s = cockpit.summarize_session(records, lambda c: True, by_cwd, main_team="atlas")
    assert s is not None and s.team == "lyon"
    # An owner session that ran `start probe` from the main checkout, then removed probe,
    # stays the main checkout's team because probe is no longer a known team.
    records = [_assistant("r1", 1, cmd="uv run python scripts/team.py start probe")]
    s = cockpit.summarize_session(
        records, lambda c: True, by_cwd, main_team="atlas", known_teams={"atlas"}
    )
    assert s is not None and s.team == "atlas"
    s = cockpit.summarize_session(
        records, lambda c: True, by_cwd, main_team="atlas", known_teams={"atlas", "probe"}
    )
    assert s is not None and s.team == "probe"


def test_activity_state_thresholds() -> None:
    now = datetime(2026, 9, 25, 1, 30, tzinfo=UTC)
    assert cockpit.activity_state("2026-09-25T01:25:00Z", now, 10, 60) == "active"
    assert cockpit.activity_state("2026-09-25T00:45:00Z", now, 10, 60) == "idle"
    assert cockpit.activity_state("2026-09-24T20:00:00Z", now, 10, 60) == "inactive"
    assert cockpit.activity_state(None, now, 10, 60) == "inactive"


def test_render_embeds_data_and_escapes_script_close() -> None:
    html = cockpit.render({"generated_at": "x", "note": "</script><b>"})
    assert "__COCKPIT_DATA__" not in html and "__COCKPIT_REFRESH__" not in html
    assert "</script><b>" not in html and "<\\/script>" in html
    assert 'http-equiv="refresh"' not in html
    assert '<meta http-equiv="refresh" content="60">' in cockpit.render({}, refresh_seconds=60)

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


ROADMAP_FULL = (
    "| Phase | Goal | Exit criteria (draft) |\n"
    "|---|---|---|\n"
    "| **1: Charter** | Decide | Charter accepted. ADRs for objective "
    "([0005](decisions/0005-objective.md)) and universe ([0006](decisions/0006-universe.md)). "
    "**The vendor decision is deferred** |\n"
    "| **2: Data foundation** | Store | Every fact has `known_at`. `data-validator` passes |\n"
    "| **3: Backtest** | Backtester | Deflated Sharpe is reported. "
    "**Price vendor ADR merged at Phase 3 start** |\n"
    "| **4: Paper** | Paper trading | At least 6 monthly rebalances (ADR 0005). "
    "The journal captures everything |\n"
    "| **5: LLM layer** *(only if an ADR says so)* | Memo | Calibration tracked |\n"
    "| **6: Small live** | ~$100 live | Stop criteria written down |\n"
    "\n"
    "## MVP scope (what Phases 2\u20134 build)\n"
    "\n"
    "The MVP is a **plain** quant system, tracked against [SPY](x) inside the same system.\n"
    "\n"
    "**Deferred beyond the MVP**: social data.\n"
)

REPORT = """# Research Report: Price vendors (G8)

**Brief:** #46  ·  **Date:** 2026-09-25  ·  **Status:** INCOMPLETE. Spent.  ·  **Agent/model:** x

## Answer
"""

ADR = """# 0005. Objective, benchmarks and stop criteria

**Status:** Accepted  ·  **Date:** 2026-09-24  ·  **Issue:** #7
"""

PLAN_HEADER = """# Plan: Data foundation (Phase 2)

**Spec:** [specs/data-foundation.md](../specs/data-foundation.md)  ·  **Status:** Draft
"""

WORK_MAP = """
[task.T5]
what = "A fixture universe."
why = "Edge cases on purpose."
unblocks = ["phase:2"]

[issue.49]
what = "Does combining signals help?"
why = "Decides Phase 3 scope."
unblocks = ["issue:51"]

[adr.0005]
what = "The objective."
"""


def test_parse_roadmap_criteria_note_and_mvp() -> None:
    phases = cockpit.parse_roadmap(ROADMAP_FULL)
    assert [p["phase"] for p in phases] == [1, 2, 3, 4, 5, 6]
    assert phases[4]["note"] == "only if an ADR says so" and phases[3]["note"] == ""
    c1 = phases[0]["criteria"]
    assert [c["text"] for c in c1] == [
        "Charter accepted.",
        "ADRs for objective (0005) and universe (0006).",
        "The vendor decision is deferred",
    ]
    assert c1[1]["adrs"] == ["0005", "0006"]
    # A bare "(ADR 0005)" mention is not a gate; only a link to the decision file is.
    assert phases[3]["criteria"][0]["adrs"] == []
    mvp = cockpit.parse_mvp(ROADMAP_FULL)
    assert (mvp["first_phase"], mvp["last_phase"]) == (2, 4)
    assert mvp["summary"] == (
        "The MVP is a plain quant system, tracked against SPY inside the same system."
    )
    assert cockpit.parse_mvp("no such section")["first_phase"] is None


def test_parse_status_decisions_skips_none() -> None:
    text = (
        "## Decisions needed from owner\n- none until T3\n- Before Phase 6: account type\n\n## Next"
    )
    assert cockpit.parse_status_decisions(text) == ["Before Phase 6: account type"]
    assert cockpit.parse_status_decisions("## Other\n- x") == []


def test_parse_research_report_and_doc_headers() -> None:
    r = cockpit.parse_research_report(REPORT, "2026-09-25-price-vendors")
    assert r == {
        "id": "2026-09-25-price-vendors",
        "title": "Price vendors (G8)",
        "status": "INCOMPLETE",
        "date": "2026-09-25",
        "brief_issue": 46,
    }
    bare = cockpit.parse_research_report("# Trial Registry\n\nrows\n", "trial-registry")
    assert bare["status"] is None and bare["title"] == "Trial Registry"
    adr = cockpit.parse_doc_header(ADR, "0005-objective")
    assert adr == {
        "id": "0005",
        "title": "Objective, benchmarks and stop criteria",
        "status": "Accepted",
        "issue": 7,
        "phase": None,
    }
    plan = cockpit.parse_doc_header(PLAN_HEADER, "data-foundation")
    assert plan["id"] == "data-foundation" and plan["phase"] == 2
    assert plan["title"] == "Data foundation" and plan["status"] == "Draft"


def test_load_work_map_and_validation() -> None:
    wm = cockpit.load_work_map(WORK_MAP)
    assert wm["task:T5"] == {
        "what": "A fixture universe.",
        "why": "Edge cases on purpose.",
        "unblocks": ["phase:2"],
    }
    assert wm["adr:0005"]["why"] == "" and wm["adr:0005"]["unblocks"] == []
    import pytest

    with pytest.raises(ValueError, match="unknown kind"):
        cockpit.load_work_map('[pr.1]\nwhat = "x"\n')
    with pytest.raises(ValueError, match="not kind:key"):
        cockpit.load_work_map('[task.T1]\nunblocks = ["T2"]\n')


def _graph() -> dict[str, object]:
    roadmap = cockpit.parse_roadmap(ROADMAP_FULL)
    mvp = cockpit.parse_mvp(ROADMAP_FULL)
    tasks = [
        {
            "id": "T4",
            "title": "Store",
            "owner": False,
            "depends_on": [],
            "state": "done",
            "holder": None,
            "plan": "p",
            "phase": 2,
        },
        {
            "id": "T5",
            "title": "Universe",
            "owner": False,
            "depends_on": ["T4"],
            "state": "in_progress",
            "holder": "orion",
            "plan": "p",
            "phase": 2,
        },
        {
            "id": "T6",
            "title": "As-of",
            "owner": False,
            "depends_on": ["T5"],
            "state": "blocked",
            "holder": None,
            "plan": "p",
            "phase": 2,
        },
        {
            "id": "T3",
            "title": "Record",
            "owner": True,
            "depends_on": [],
            "state": "ready",
            "holder": None,
            "plan": "p",
            "phase": 2,
        },
    ]
    issues = [
        {"number": 22, "title": "T5: Universe", "updated": "", "labels": ["team:orion", "task:T5"]},
        {
            "number": 49,
            "title": "research G4",
            "updated": "",
            "labels": ["type:research", "team:finneas"],
        },
        {
            "number": 51,
            "title": "spec+plan: Phase 3 backtest",
            "updated": "",
            "labels": ["type:docs", "size:L"],
        },
        {"number": 52, "title": "decision: LLM role", "updated": "", "labels": ["type:decision"]},
    ]
    prs = [
        {
            "number": 31,
            "title": "T5",
            "branch": "feat/22-x",
            "draft": True,
            "updated": "",
            "ci": "pass",
            "labels": [],
            "issue": 22,
        },
        {
            "number": 88,
            "title": "G4",
            "branch": "research/49-x",
            "draft": False,
            "updated": "",
            "ci": "pending",
            "labels": [],
            "issue": 49,
        },
    ]
    reports = [
        cockpit.parse_research_report(REPORT, "2026-09-25-price-vendors"),
        {"id": "g4-report", "title": "G4", "status": "COMPLETE", "date": "", "brief_issue": 49},
    ]
    adrs = [{**cockpit.parse_doc_header(ADR, "0005-objective"), "file": "0005-objective.md"}]
    docs = [{**cockpit.parse_doc_header(PLAN_HEADER, "data-foundation"), "kind": "plan"}]
    work_map = cockpit.load_work_map(WORK_MAP)
    return cockpit.build_graph(
        roadmap=roadmap,
        current_phase=2,
        mvp=mvp,
        tasks=tasks,
        issues=issues,
        prs=prs,
        reports=reports,
        adrs=adrs,
        docs=docs,
        work_map=work_map,
        repo_url="https://gh/x/y",
    )


def test_build_graph_nodes_states_and_folding() -> None:
    g = _graph()
    by_id = {n["id"]: n for n in g["nodes"]}
    # A plan task folds in its canonical issue and open PR; an open PR means "in review".
    t5 = by_id["task:T5"]
    assert t5["state"] == "in_review" and t5["issue"] == 22 and t5["holder"] == "orion"
    assert [p["number"] for p in t5["prs"]] == [31] and t5["url"] == "https://gh/x/y/issues/22"
    assert "issue:22" not in by_id
    assert by_id["task:T4"]["state"] == "done" and by_id["task:T6"]["state"] == "blocked"
    assert by_id["task:T3"]["owner"] is True and by_id["task:T3"]["state"] == "ready"
    # Issues are typed by label; a research report folds into its open brief issue.
    assert by_id["issue:49"]["kind"] == "research" and by_id["issue:49"]["state"] == "in_review"
    assert by_id["issue:49"]["report_status"] == "COMPLETE" and "research:g4-report" not in by_id
    assert by_id["issue:51"]["kind"] == "docs" and by_id["issue:51"]["state"] == "open"
    assert by_id["issue:52"]["kind"] == "decision"
    # A report whose brief is closed stands on its own; ADRs and plans are done.
    assert by_id["research:2026-09-25-price-vendors"]["state"] == "done"
    assert by_id["research:2026-09-25-price-vendors"]["report_status"] == "INCOMPLETE"
    assert by_id["adr:0005"]["state"] == "done" and by_id["plan:data-foundation"]["state"] == "done"
    # Phases: state relative to the current one, MVP flag from the roadmap range.
    assert by_id["phase:1"]["state"] == "done" and by_id["phase:2"]["state"] == "current"
    assert by_id["phase:5"]["state"] == "future" and by_id["phase:5"]["note"]
    assert [n["mvp"] for n in g["nodes"] if n["kind"] == "phase"] == [
        False,
        True,
        True,
        True,
        False,
        False,
    ]
    # Words come from the map; ids without an entry are listed.
    assert by_id["task:T5"]["what"] == "A fixture universe." and by_id["task:T4"]["what"] == ""
    assert "task:T4" in g["missing_map_entries"] and "task:T5" not in g["missing_map_entries"]


def test_build_graph_edges() -> None:
    g = _graph()
    edges = {(e["from"], e["to"]): e["kind"] for e in g["edges"]}
    assert edges[("task:T4", "task:T5")] == "depends" and edges[("task:T5", "task:T6")] == "depends"
    # Sink tasks gate their phase; upstream tasks do not.
    assert edges[("task:T6", "phase:2")] == "gate" and edges[("task:T3", "phase:2")] == "gate"
    assert ("task:T4", "phase:2") not in edges
    # The plan file gates its phase; a linked ADR gates the phase whose criteria link it.
    assert edges[("plan:data-foundation", "phase:2")] == "gate"
    assert edges[("adr:0005", "phase:1")] == "gate" and ("adr:0005", "phase:4") not in edges
    # Work-map links, only between nodes that exist; T5 → phase:2 is de-duplicated with the gate.
    assert edges[("issue:49", "issue:51")] == "unblocks"
    assert edges[("task:T5", "phase:2")] in ("unblocks", "gate")
    assert sum(1 for e in g["edges"] if e["from"] == "task:T5" and e["to"] == "phase:2") == 1
    # Phases chain in order; the optional phase 5 is bypassed by 4 → 6.
    assert edges[("phase:1", "phase:2")] == "phase" and edges[("phase:4", "phase:5")] == "phase"
    assert edges[("phase:4", "phase:6")] == "phase" and ("phase:5", "phase:6") not in edges
    # Unlinked ADR references (0006 has no ADR file here) are dropped.
    assert all(e["from"] != "adr:0006" for e in g["edges"])


def test_phase_progress_counts_spec_issue_and_criteria() -> None:
    g = _graph()
    roadmap = cockpit.parse_roadmap(ROADMAP_FULL)
    issues = [{"number": 51, "title": "spec+plan: Phase 3 backtest", "labels": ["type:docs"]}]
    adrs = [{"id": "0005", "status": "Accepted"}]
    prog = cockpit.phase_progress(
        roadmap, 2, cockpit.parse_mvp(ROADMAP_FULL), g["nodes"], issues, adrs
    )
    by_phase = {p["phase"]: p for p in prog}
    p2 = by_phase[2]
    assert p2["state"] == "current" and p2["planned"] and p2["total"] == 4 and p2["mvp"]
    assert p2["counts"]["done"] == 1 and p2["counts"]["in_review"] == 1
    assert p2["counts"]["blocked"] == 1 and p2["counts"]["ready"] == 1
    assert [c["state"] for c in p2["criteria"]] == ["needs_owner", "needs_owner"]
    p3 = by_phase[3]
    assert not p3["planned"] and p3["spec_issue"] == {
        "number": 51,
        "title": "spec+plan: Phase 3 backtest",
        "holder": None,
    }
    # Phase 1 is behind us: every criterion counts as done. Phase 4's bare ADR mention does not.
    assert all(c["state"] == "done" for c in by_phase[1]["criteria"])
    assert by_phase[4]["criteria"][0]["state"] == "needs_owner"
    # A future phase whose criteria link an accepted ADR is derivable; a missing ADR is flagged.
    linked = cockpit.parse_roadmap(
        "| **7: X** | g | Done when [0005](decisions/0005-o.md) and "
        "[0009](decisions/0009-z.md) exist. Also [0005](decisions/0005-o.md) |\n"
    )
    p7 = cockpit.phase_progress(linked, 2, {"first_phase": None, "last_phase": None}, [], [], adrs)[
        0
    ]
    assert [c["state"] for c in p7["criteria"]] == ["missing_adr", "done"] and not p7["mvp"]

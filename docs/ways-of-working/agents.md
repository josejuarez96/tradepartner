# Agents

**Status:** Accepted v1.0 (PR #2, 2026-09-24)

## Two kinds of agents: keep them separate

| | **Build agents** (this doc) | **Product agents** |
|---|---|---|
| What | Claude Code subagents that help *build* TradePartner | LLM components *inside* TradePartner, such as an analyst memo writer |
| Where | `.claude/agents/*.md` | `src/tradepartner/llm/` (future) |
| Decided by | This doc | A spec and an ADR in Phase 1 (handoff §12 decisions 8–10) |

This doc covers build agents only. Whether TradePartner has an LLM layer at all is still an open product decision.

## Roster

Six agents. That is deliberately few: each one owns a job the main session does badly or inconsistently. Add an agent only after doing the same job by hand about three times and seeing it go wrong.

| Agent | Stage | Job | Writes? | Model |
|---|---|---|---|---|
| `researcher` | Research | Answers **one** bounded research brief, with evidence tiers, grades and a mandatory disconfirmation search | Only `docs/research/` | opus |
| `spec-critic` | Spec / Plan | Adversarially reviews a spec, plan or ADR for gaps, untestable criteria, hidden scope, and domain traps (look-ahead, overfitting) | No (read-only) | opus |
| `implementer` | Build | Executes **one** plan task on its own branch, tests first, and opens a draft PR. Doesn't expand scope | Code + tests | sonnet |
| `quant-auditor` | Review | Audits diffs touching data, backtests or signals for look-ahead bias, survivorship, point-in-time violations, cost modeling and trial logging | No (read-only) | opus |
| `safety-reviewer` | Review | Audits diffs touching the broker, orders, secrets, or LLM inputs/outputs: order isolation, idempotency, kill switch, prompt injection, key handling | No (read-only) | opus |
| `doc-keeper` | Record | After a merge or at session end: updates `STATUS.md`, `CHANGELOG.md` and plan checkboxes, and flags drift between docs and code | Only `docs/`, `CHANGELOG.md` | haiku |

**General code review** uses the built-in `/code-review` command. We don't need our own generic reviewer.

**Architecture and planning** use the main session, or the built-in `Plan` agent, driven by the owner. That's where the owner's judgment matters most, so it isn't delegated to a custom agent.

## Who runs what, by stage

```
Research:  owner writes brief (issue) ──► researcher ──► report PR ──► owner reads, decides
Decide:    main session drafts ADR ──► spec-critic ──► owner merges
Spec/Plan: main session drafts ──► spec-critic ──► owner merges
Build:     team claims task (scripts/team.py) ──► implementer × N (parallel worktrees, non-overlapping tasks) ──► draft PRs
Review:    /code-review + quant-auditor and/or safety-reviewer (by paths touched) ──► owner merges
Record:    doc-keeper
```

## Orchestrator windows and model tiers

Any number of Claude Code chat windows may build in parallel; each one is a **team** ([teams.md](teams.md)). The window itself is the orchestrator: it claims work, spawns the agents below, triages reviews and reports to the owner. Pick the model by how far a wrong judgment propagates, not by habit.

| Work | Model | Why |
|---|---|---|
| Orchestrator window during Build and Review | **Opus 5.5** | Claims, delegation and review triage: capable, and cheaper than Fable |
| Spec, plan and ADR drafting; phase retros; cross-team conflict resolution; changes to the ways-of-working docs | **Fable 5.1** | Errors here land in every later PR |
| `implementer` | Sonnet (roster) | One scoped task with a plan line and tests |
| `researcher`, `spec-critic`, `quant-auditor`, `safety-reviewer` | Opus (roster) | Judgment-heavy, read-only or doc-only |
| `doc-keeper` | Haiku (roster) | Mechanical |

Start a window on Opus 5.5 by default. Escalate to Fable only for the second row, and say so in the PR description when you did.

## Guardrails against agents "running wild"

What went wrong in the trading-research conversation:
- Research was launched twice and returned nothing.
- Disconfirmation searches were skipped.
- Findings leaned on Tier 3 sources.

The rules that address it:

1. **Every research task starts from a written brief.** Use the [research brief template](../templates/research-brief.md): one question, the decision it unblocks, scope, allowed source tiers, stop condition, and a budget (max sources). No brief, no research.
2. **One question per agent run.** Eight gaps means eight briefs and eight runs, possibly in parallel, not one mega-prompt.
3. **Fixed output shape.** Reports use the [research report template](../templates/research-report.md). A report missing its disconfirmation section is incomplete, and the owner rejects the PR.
4. **Artifacts, not chat.** Agents write to files in a branch. If a run dies, the partial file is still there.
5. **Least privilege.** Reviewers are read-only. The researcher can only write to `docs/research/`. No agent can push to `main` or read `.env` (enforced in `.claude/settings.json`). Subagents never merge. The main session merges only when the owner explicitly says to merge that specific PR, and `gh pr merge` always asks for confirmation.
6. **Scope lock.** The implementer does exactly one plan task. When it discovers extra work, it opens an issue instead of doing it.

## Adding or changing an agent

- Agents are code: change them through a PR, with the reason in the description.
- Each agent file states its purpose, its inputs, its output format, and what it must never do.
- Review the roster in every phase retro. Delete agents that aren't being used.

## Candidates, not yet created

Create these when a phase needs them:

- `data-validator` (Phase 2): checks a new data source for gaps, splits, delisting coverage, timezone handling and stale rows.
- `backtest-runner` (Phase 3): runs a registered hypothesis against the backtester and appends to the trial registry. It never touches the holdout without an explicit flag.
- `journal-analyst` (Phase 4+): produces the weekly calibration, cost and attribution report from the trade journal.

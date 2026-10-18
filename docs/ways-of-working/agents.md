# Agents

**Status:** Accepted v1.0 (PR #2, 2026-09-24; [changes](#changes) at the end)

This doc covers **build agents**: Claude Code subagents in `.claude/agents/*.md` that help build TradePartner. **Product agents**, LLM components inside TradePartner (`src/tradepartner/llm/`, future), are decided by a spec and an ADR (handoff §12 decisions 8–10); whether there is an LLM layer at all is still an open product decision.

## Roster

Seven agents, deliberately few: each owns a job the main session does badly or inconsistently. Add one only after doing the same job by hand about three times and seeing it go wrong.

| Agent | Stage | Job | Writes? | Model |
|---|---|---|---|---|
| `researcher` | Research | Answers **one** bounded research brief, with evidence tiers, grades and a mandatory disconfirmation search | Only `docs/research/` | opus |
| `spec-critic` | Spec / Plan | Adversarially reviews a spec, plan or ADR for gaps, untestable criteria, hidden scope, and domain traps (look-ahead, overfitting) | No (read-only) | opus |
| `implementer` | Build | Optional sub-writer: executes **one** plan task on its own branch, tests first, opens a draft PR, never expands scope | Code + tests | sonnet |
| `quant-auditor` | Review | Audits diffs touching data, backtests or signals for look-ahead, survivorship, point-in-time violations, cost modelling and trial logging | No (read-only) | opus |
| `safety-reviewer` | Review | Audits diffs touching the broker, orders, secrets or LLM inputs/outputs: order isolation, idempotency, kill switch, prompt injection, key handling | No (read-only) | opus |
| `backtest-runner` | Build / Review | Runs **one** hypothesis file (or `run_sweep`, strategy-lab T107, when asked) on a temp-file copy of the fixture store it builds itself, `store_path` always set so its trials are synthetic and never reach the owner's registry, inside the fixture's 2018-2020 window; reports metrics, gap and refusals. Never a holdout or override flag, never the main checkout; its scripts run through `uv run python`, which stays off the allow list | Only its scratchpad | sonnet |
| `doc-keeper` | Record | After a merge or at session end: updates `STATUS.md`, `CHANGELOG.md` and plan checkboxes; flags drift between docs and code | Only `docs/`, `CHANGELOG.md` | haiku |

General code review is the built-in `/code-review`. Architecture and planning stay with the main session or the built-in `Plan` agent, driven by the owner; when a plan is consumed, the [planning team](teams.md#picking-work) takes that duty on a trigger, with caps.

| Stage | Who runs what |
|---|---|
| Research | owner writes the brief (issue) → `researcher` → report PR → owner reads, decides |
| Decide, Spec, Plan | main session drafts → `spec-critic` → owner merges |
| Frontier | planning team on its trigger → spec, plan or chaining amendment PR → `spec-critic` → owner merges; or a decision memo on the question's issue → owner decides |
| Build | team claims the task (`scripts/team.py`) and writes the code; `implementer` optional, one per task, disjoint files → draft PR |
| Review | `/code-review` + `quant-auditor` and/or `safety-reviewer` by paths → landing per [git-workflow rule 7](git-workflow.md#merge-classes-rule-7) |
| Record | `doc-keeper` |

## Orchestrator windows and model tiers

Each window is a **team** ([teams.md](teams.md)); the window is the orchestrator of its own agents: it claims, spawns the agents above, triages reviews and reports. Pick the model by how far a wrong judgment propagates, not by habit. Read the table top-down; the first row that matches wins.

| Work | Model | Why |
|---|---|---|
| Teams by default. Always: the orchestrator window; any plan task whose `Review:` names `safety-reviewer` (sizing, risk checks, order ids, the wrapper, the run, resume, the kill switch, pure or not); any fix touching a `SAFETY_PREFIXES` path or a secret; a docs task touching `.claude/agents/` or `docs/runbooks/`, alone or with ways-of-working changes (those paths steer reviewers and live procedures) | **Opus 5.5** | Judgment on the path where a mistake places an order |
| Only when the orchestrator names it in the spawn: a docs-only task outside the rows above and below; a plan task whose `Review:` does **not** name `safety-reviewer` (a pure module, a page, a report); a `size:S` fix touching no `SAFETY_PREFIXES` path (`scripts/ready_pr.py` holds the list) and no secret | **Sonnet 5** | The plan line is the contract |
| Spec, plan and ADR drafting; phase retros; cross-team conflict resolution; ways-of-working changes | **Fable 5.1** | Errors here land in every later PR |
| `implementer`, `backtest-runner` | Sonnet (roster) | One scoped task; one scripted fixture run |
| `researcher`, `spec-critic`, `quant-auditor`, `safety-reviewer` | Opus (roster) | Judgment-heavy, read-only or doc-only |
| `doc-keeper` | Haiku (roster) | Mechanical |

- The spawn names the model; a window whose assignment names none runs on Opus 5.5. Fable only for its row, and say so in the PR description.
- A Sonnet window that meets a judgment call its task line does not settle (a BLOCKER it cannot resolve, a conflict with another team's line) reports `blocked`; it does not guess.
- `/code-review` inherits the window's model, so it runs from an Opus or Sonnet window, never a Fable one: one round at `medium`, at most one verification round at `low`.

## Review passes

1. **Pass 1 is the review.** All required reviewers start together, on the same pushed commit, once per gate; they are not restarted because `main` was merged in (CI reviews that merge). The window fixes every BLOCKER and SHOULD FIX in one commit, not one per finding. A pass-1 finding under about 15 minutes is fixed in that commit too, so the verification pass reads it.
2. **One verification pass, only for a reviewer whose verdict was not `PASS`.** Every reviewer comment names the head commit it reviewed. The window hands the reviewer its pass-1 comment; the reviewer reads the PR's own changes since pass 1 (`git diff <pass-1 head>..HEAD` over the PR's own files, skipping what a merge of `main` brought in: the [verification-pass runbook](../runbooks/verification-pass-diff.md)) and confirms each fix in the file as it stands now. `PASS` when every pass-1 BLOCKER and SHOULD FIX is fixed and that diff holds no BLOCKER. `FAIL` when one is unfixed, when the diff holds a BLOCKER, or when it holds a new SHOULD FIX on the order path (anything that submits, cancels, halts, resumes or sizes an order; the risk checks, order ids, kill switch, run lock) or a secret: there a SHOULD FIX is never a follow-up. A new SHOULD FIX off the order path, and anything else noticed, goes under "Follow-ups" in the comment and does not change the verdict.
3. **Nits never cause a pass.** A reviewer that said `PASS` is not re-run; a nit-only fix gets no re-pass. A third pass only after a `FAIL`: the window fixes, runs one more verification pass over the diff since pass 2, and says on the PR why.
4. **The order path keeps its floor.** `ready_pr.py`'s gate is unchanged: the latest verdict of each required reviewer must be `PASS`. Follow-ups are NITs and non-blocking findings off the order path and away from secrets only, sorted before ready. `spec-critic` follows the same count: `APPROVE WITH CHANGES`, the fixes, one verification pass.
5. **The follow-up rule.** Each non-blocking finding goes one of three ways. **Fix it in this PR** when it is a pass-1 finding under about 15 minutes (rule 1); a follow-up the verification pass lists is never fixed in the PR, that would be a commit no pass reads. **Drop it** when it is a true nit: style, wording, "consider", a refactor, an extra test for behaviour already correct; it stays listed in the comment. **File it** only when it is a real defect (wrong output, a crash, a look-ahead or point-in-time hole, a safety gap, or a missing test guarding one of those) or an owner decision: into the unbuilt consumer's task line, or **one** `size:S` issue for the PR, linked on it ([plan shape](development-process.md#plan-shape-many-windows-one-plan) rule 6).

## Parallelism inside a team

A team may run several subagents at once **inside its own claimed scope** and nowhere else. Speed comes from independent things at the same time, never from more hands on one file.

| Situation | In parallel | One at a time |
|---|---|---|
| Several claimed tasks | One writer per task, each in its own worktree, when the file lists are disjoint | Tasks that touch the same module |
| One task being built | Read-only helpers next to the one writer: `Explore` for codebase questions, `spec-critic` on the design, `researcher` on a bounded question | Writing code: **one writer per branch**; two agents on one branch, even different files, race on commits and the tree |
| A diff ready for review | `quant-auditor`, `safety-reviewer` and `/code-review` on the **same commit**, findings triaged together | Re-review after fixes (new commit, same fan-out) |
| Fixes from a review | One writer applies them | Never a fixer per finding |

Subagents never claim, release, mark ready or merge; the window does those through `scripts/team.py` and `/ready-pr` (a team run as a background subagent is that window for its own items, [teams.md](teams.md#vocabulary); the helpers it starts still do none of these). Work outside the claim becomes an issue, not an extra agent; new agent types land only through a PR the owner merges. A subagent cannot spawn subagents: depth is one level, breadth is the window's call within these rows. Parallelism is bounded by the plan's dependency graph: when the frontier is one task, one window is enough; the rest pick research, decision or docs issues ([Picking work](teams.md#picking-work)).

## Guardrails against agents "running wild"

1. **Every research task starts from a written brief** ([template](../templates/research-brief.md)): one question, the decision it unblocks, scope, allowed source tiers, stop condition, a budget (max sources). No brief, no research.
2. **One question per agent run.** Eight gaps means eight briefs and eight runs, not one mega-prompt.
3. **Fixed output shape** ([report template](../templates/research-report.md)). A report missing its disconfirmation section is incomplete; the owner rejects the PR.
4. **Artifacts, not chat.** Agents write to files in a branch; if a run dies, the partial file is still there.
5. **Least privilege.** Reviewers are read-only; the researcher writes only to `docs/research/`; no agent can push to `main` or read `.env` (`.claude/settings.json`). Subagents and team windows never merge ([rule 7](git-workflow.md#merge-classes-rule-7)); `gh pr merge` always asks for confirmation.
6. **Scope lock.** One plan task per writer. Extra work it discovers becomes an issue if it is a real defect or an owner decision; a nit is dropped.

## Adding or changing an agent

Agents are code: change them through a PR, with the reason in the description. Each agent file states its purpose, inputs, output format and what it must never do. Review the roster in every phase retro; delete agents not being used. Candidates for later phases: `data-validator` (Phase 2: gaps, splits, delisting coverage, timezone handling, stale rows in a new data source) and `journal-analyst` (Phase 4+: the weekly calibration, cost and attribution report from the trade journal).

## Changes

| Date | Change | Where |
|---|---|---|
| 2026-09-24 | v1.0; guardrails after the trading-research conversation (research launched twice, disconfirmation skipped, Tier 3 sources) | PR #2 |
| 2026-09-25 | Parallelism inside a team | #73 |
| 2026-10-01 | Model tiers; passes capped after two PRs took four `safety-reviewer`, two `spec-critic` and three `/code-review` rounds | #489 |
| 2026-10-04 | Planning team; background subagents count as the window; tier table first match wins | #782, #773, #631 |
| 2026-10-07 | Follow-up rule, after 93 open `size:S` issues, 70 of them nits, done or stale | #1123 |
| 2026-10-18 | Rewritten for length; review-pass rule written once as practised (verification only for a non-PASS verdict, no re-pass on nits); git recipe moved to a runbook; Opus default for teams; `implementer` optional | #1752 |

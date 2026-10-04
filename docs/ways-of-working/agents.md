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

Seven agents. That is deliberately few: each one owns a job the main session does badly or inconsistently. Add an agent only after doing the same job by hand about three times and seeing it go wrong.

| Agent | Stage | Job | Writes? | Model |
|---|---|---|---|---|
| `researcher` | Research | Answers **one** bounded research brief, with evidence tiers, grades and a mandatory disconfirmation search | Only `docs/research/` | opus |
| `spec-critic` | Spec / Plan | Adversarially reviews a spec, plan or ADR for gaps, untestable criteria, hidden scope, and domain traps (look-ahead, overfitting) | No (read-only) | opus |
| `implementer` | Build | Executes **one** plan task on its own branch, tests first, and opens a draft PR. Doesn't expand scope | Code + tests | sonnet |
| `quant-auditor` | Review | Audits diffs touching data, backtests or signals for look-ahead bias, survivorship, point-in-time violations, cost modeling and trial logging | No (read-only) | opus |
| `safety-reviewer` | Review | Audits diffs touching the broker, orders, secrets, or LLM inputs/outputs: order isolation, idempotency, kill switch, prompt injection, key handling | No (read-only) | opus |
| `backtest-runner` | Build / Review | Runs **one** hypothesis file on a temp-file copy of the fixture store it builds itself (`store_path` always set, so its trials are synthetic and never reach the owner's registry), inside the fixture's 2018-2020 window, and reports metrics, gap and refusals. Never passes a holdout or override flag, never enters the main checkout. Its scripts run through `uv run python`, which must stay off the allow list | Only its scratchpad | sonnet |
| `doc-keeper` | Record | After a merge or at session end: updates `STATUS.md`, `CHANGELOG.md` and plan checkboxes, and flags drift between docs and code | Only `docs/`, `CHANGELOG.md` | haiku |

**General code review** uses the built-in `/code-review` command. We don't need our own generic reviewer.

**Architecture and planning** use the main session, or the built-in `Plan` agent, driven by the owner. That's where the owner's judgment matters most, so it isn't delegated to a custom agent. When the plan is consumed, the **planning team** ([teams.md, Picking work](teams.md#picking-work), #782) takes the same duty on a trigger, with caps: a Fable-tier team the orchestrator spawns, not a new agent file.

## Who runs what, by stage

```
Research:  owner writes brief (issue) ──► researcher ──► report PR ──► owner reads, decides
Decide:    main session drafts ADR ──► spec-critic ──► owner merges
Spec/Plan: main session drafts ──► spec-critic ──► owner merges
Frontier:  orchestrator spawns the planning team on its trigger (teams.md, Picking work) ──► next roadmap spec or plan PR, a chaining plan amendment, or a decision memo ──► spec-critic ──► owner merges
Build:     team claims task (scripts/team.py) ──► implementer × N (parallel worktrees, non-overlapping tasks) ──► draft PRs
Review:    /code-review + quant-auditor and/or safety-reviewer (by paths touched) ──► owner merges
Record:    doc-keeper
```

## Orchestrator windows and model tiers

Any number of Claude Code chat windows may build in parallel; each one is a **team** ([teams.md](teams.md)). The window itself is the orchestrator: it claims work, spawns the agents below, triages reviews and reports to the owner. Pick the model by how far a wrong judgment propagates, not by habit. Read the table top-down; the first row that matches the work wins (owner decision on #631).

| Work | Model | Why |
|---|---|---|
| The orchestrator window; a team window on any plan task whose `Review:` field names `safety-reviewer` (sizing, risk checks, order ids, the wrapper, the run, resume, the kill switch included, pure or not); on any issue whose fix touches the paths the next row excludes, a docs-only task that touches `.claude/agents/` or `docs/runbooks/` included, and a ways-of-working change that also touches either (those paths steer the reviewers and the owner's live procedures, so a mixed change stops at this row); or on a task the orchestrator marks as needing it | **Opus 5.5** | Review triage and judgment on the path where a mistake places an order |
| A team window on a docs-only task that neither the row above nor the Fable row names; on a plan task whose `Review:` field does **not** name `safety-reviewer` (a pure module, a page, a report); or on a `size:S` issue whose fix touches no path in `SAFETY_PREFIXES` (`scripts/ready_pr.py`; that constant is the list, this row does not repeat it) and nothing that handles a secret | **Sonnet 5** | The plan line is the contract; the window claims, hands it to an `implementer`, runs the reviewers and `/ready-pr`. Windows were 56% of all tokens on Opus (#489) |
| Spec, plan and ADR drafting; phase retros; cross-team conflict resolution; changes to the ways-of-working docs | **Fable 5.1** | Errors here land in every later PR |
| `implementer`, `backtest-runner` | Sonnet (roster) | One scoped task with a plan line and tests; one scripted fixture run |
| `researcher`, `spec-critic`, `quant-auditor`, `safety-reviewer` | Opus (roster) | Judgment-heavy, read-only or doc-only |
| `doc-keeper` | Haiku (roster) | Mechanical |

The orchestrator names the model in the assignment; a window whose assignment names none starts on Sonnet 5 for the second row's work and on Opus 5.5 otherwise. Escalate to Fable only for the third row, and say so in the PR description when you did. A Sonnet window that meets a judgment call its task line does not settle (a reviewer BLOCKER it cannot resolve, a conflict with another team's line) reports `blocked` to the orchestrator; it does not guess.

`/code-review` forks from the window that runs it and inherits that window's model, so it is run from an Opus or a Sonnet window, never from a Fable one: one round at `medium`, then at most one verification round at `low` (#489).

## Review passes

Added 2026-10-01 (#489). Two PRs on 2026-09-30 took four `safety-reviewer` passes, two `spec-critic` passes and three `/code-review` rounds between them, because every pass re-read the whole diff and found something new in the text the last fix had added. A pass is an Opus session; the cap below would have halved that.

1. **Two passes per reviewer per PR.** Pass 1 is the review. The window fixes every BLOCKER and SHOULD FIX in one commit, not one commit per finding.
2. **Pass 2 is a verification pass over everything pushed to the PR's own files since pass 1.** Every reviewer comment names the head commit it reviewed. The window gives the reviewer its pass-1 comment and asks it to verify the fixes by reading `git diff <pass-1 head>..HEAD`, so no commit pushed after pass 1 goes unreviewed. Commits a merge of `main` brought in were reviewed on their own PRs and are skipped: the reviewer fetches `origin/main` first (stale, it puts files only the merge touched in the second list) and scopes that diff to the PR's own files as they stood at pass 1 and as they stand now (a file reset to `main`'s version after pass 1 is in the first list, a file added after pass 1 in the second), `{ git diff --name-only -z origin/main...<pass-1 head>; git diff --name-only -z origin/main...HEAD; } | sort -zu | xargs -0 -r git diff <pass-1 head>..HEAD --` (NUL-separated, so a path with a space stays one path; `-r` runs nothing when the list is empty). That diff shows how a conflict in those files was resolved, and, when `main` changed one of them since pass 1, `main`'s hunks in it next to the resolution; those hunks are not the PR's changes. Two read-only commands tell them apart: `git log -p --first-parent --no-merges <pass-1 head>..HEAD -- <those files>` shows only the PR's own hunks (the merge commits are left out), and `git show --remerge-diff <sha>` for each merge that `git log --first-parent --merges --format=%H <pass-1 head>..HEAD` lists shows only what the resolution changed against git's automatic merge, a resolution that takes one side whole included; a hunk in neither came from `main` and is skipped, except in a file on the order path or one that handles a secret, where every hunk is read. The reviewer also confirms each pass-1 fix in the file as it stands now, not only in the diff, so a fix lost to a resolution is caught. The verdict is `PASS` only when every pass-1 BLOCKER and SHOULD FIX is fixed and that diff holds no BLOCKER. It is `FAIL` when any pass-1 BLOCKER or SHOULD FIX is not fixed, when the diff holds a BLOCKER, or when the diff holds a new SHOULD FIX on the order path (anything that submits, cancels, halts, resumes or sizes an order, the risk checks, the order ids, the kill switch, the run lock) or a secret: there a SHOULD FIX blocks, it is never a follow-up. Anything else the reviewer notices goes under a "Follow-ups" heading in its comment and does not change the verdict; the window files those as **one** `size:S` issue (plan-shape rule 6) and links it on the PR.
3. **A third pass needs a `FAIL`.** If pass 2 ends `FAIL`, the window fixes and runs one more verification pass over the diff since pass 2, and says on the PR why. A NIT never causes a pass.
4. **The order path keeps its floor.** `ready_pr.py`'s gate is unchanged: the latest verdict of each required reviewer must be `PASS`. Follow-ups are therefore NITs and non-blocking findings off the order path only; they are filed before the PR is marked ready.
5. `spec-critic` follows the same count: `APPROVE WITH CHANGES`, the fixes, one verification pass. The critic has no Bash, so the window's verification brief names the PR's own commits since pass 1 (`git log --first-parent --no-merges <pass-1 head>..HEAD`) and any file whose merge conflict it resolved.

## Parallelism inside a team

A team window may run several subagents at once **inside its own claimed scope**, and nowhere else (#73). Speed comes from doing independent things at the same time, never from more hands on one file.

| Situation | Run in parallel | One at a time |
|---|---|---|
| Several claimed tasks | One `implementer` per task, each in its own worktree, when the tasks' file lists are disjoint | Tasks that touch the same module |
| One task being built | Read-only helpers next to the one implementer: `Explore` for codebase questions, `spec-critic` on the design, `researcher` on a bounded question the task raised | Writing code: **one writer per branch**. Two agents editing one branch, even different files, race on commits and the working tree |
| A diff ready for review | `quant-auditor`, `safety-reviewer` and `/code-review` on the **same commit**, then triage all findings together | Re-review after fixes (new commit, same fan-out) |
| Fixes from a review | One implementer applies them | Never a fixer per finding on one branch |

What stays true regardless of how many agents run:
- Subagents never claim, release, mark ready or merge. The window does those, through `scripts/team.py` and `/ready-pr`.
- Work outside the claim becomes an issue, not an extra agent.
- New agent types land only through a PR the owner merges (see "Adding or changing an agent"). A window does not invent one mid-task.
- A subagent cannot spawn subagents; the platform removes that tool from them. Depth is one level, breadth is the window's call within these rows.
- Parallelism is bounded by the plan's dependency graph. When the frontier is one task, one window is enough; the rest pick research, decision or docs issues ([teams.md](teams.md), Picking work).

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
- `journal-analyst` (Phase 4+): produces the weekly calibration, cost and attribution report from the trade journal.

# Development Process

**Status:** Accepted v1.0 (PR #2, 2026-09-24; [changes](#changes) at the end)

**Research serves decisions, decisions feed specs, and specs gate code.** Every stage produces a small, named artifact, so any session, human or agent, can pick up where the last one stopped.

## The lifecycle

0. **Charter**, once per project: why, success/stop criteria, scope → [docs/charter.md](../charter.md).
1. **Research**, only if a decision is blocked → `docs/research/YYYY-MM-DD-<slug>.md`.
2. **Decide**, an ADR for anything hard to reverse → `docs/decisions/NNNN-<slug>.md`.
3. **Spec**, what and why, acceptance criteria → `docs/specs/<feature>.md`.
4. **Plan**, how: ordered tasks, one task = one PR → `docs/plans/<feature>.md`.
5. **Build**: branch, tests first, PR → code + tests.
6. **Review**: CI + specialist agents (each agent's full report a PR comment, its return a ten-line summary) → merged PR ([git-workflow rule 7](git-workflow.md#merge-classes-rule-7)).
7. **Record**: STATUS, CHANGELOG, plan ticks → `docs/STATUS.md`.
8. **Retro**, per phase: what to change in how we work → `docs/retros/phase-N.md`.

Steps 1 to 7 run per feature or phase.

### Gates: what must be true to move forward

| From → To | Gate | Who approves |
|---|---|---|
| Research → Decide | The report answers the brief's question, with evidence grades and a disconfirmation section | Owner |
| Decide → Spec | ADR status is `Accepted` | Owner |
| Spec → Plan | Acceptance criteria are testable, out-of-scope is listed, `spec-critic` has run | Owner |
| Plan → Build | Each task names its files, tests and dependencies; no task over about 400 lines; the plan passes [Plan shape](#plan-shape-many-windows-one-plan) | Owner (can be delegated for size M) |
| Build → Merge | The [Definition of Done](#definition-of-done-before-merge) | [git-workflow rule 7](git-workflow.md#merge-classes-rule-7) |
| Phase → next phase | Exit criteria met, retro written, release tagged | Owner |

Specs, plans and ADRs are class B; they land through PRs like code does, so **the approval is recorded in git history.**

## Right-size the ceremony

Size is set on the issue.

| Size | Example | Required |
|---|---|---|
| **S** (under 1 session) | Fix a typo, bump a dep, small bug | Issue → PR |
| **M** (1–3 sessions) | Add a data source, add a report | Issue with spec-lite (acceptance criteria in the body) → PR(s) |
| **L** (multi-PR feature or phase) | Backtester, broker integration, LLM layer | Spec + plan, and an ADR for any hard-to-reverse choice |

**Always an ADR, whatever the size:** choosing a vendor, data source, broker, model, storage format or library that would be painful to swap, and any change to risk rules or LLM authority.

## Plan shape (many windows, one plan)

A plan or plan amendment is approved only when it passes this list. `spec-critic` checks it (check 5); `uv run python scripts/team.py graph` prints the numbers.

1. **Slice by file, not by step.** A module that would be built in slices is split into pure modules over explicit inputs, each its own task and file, plus one driver task that composes them last. Tasks that edit one file in sequence are allowed only as a declared chain of at most three (the driver and its wiring), named in the plan's lanes paragraph.
2. **Depth bound.** No chain of open tasks longer than six PRs without a sentence in the approach section saying why.
3. **File bound.** No file named by more than two unticked tasks unless the lanes paragraph lists them as one chain, in order, or the plan states that they touch disjoint parts (config keys, dated amendment notes) and that whichever lands later merges main first.
4. **Owner tasks gate the edge, never the middle.** An owner task (keys, the real store, market hours) gates only the task that consumes its artifact. A build task that would otherwise wait on it declares the stub on its line: the one call site, the test that names it, and that the follow-up lands by amendment or a size-S issue.
5. **Handoffs amend the line.** A PR that learns something a later task needs edits that task's line in the same PR. A docstring, a PR body or a review comment is not a handoff.
6. **Fixes on an unbuilt consumer's input go to its line, or one issue per module.** Review findings on a merged module an unbuilt task consumes are folded into that task's line, or batched as one `size:S` issue per PR, per module; never one issue per finding. Only real defects and owner decisions are filed; nits are dropped ([follow-up rule](agents.md#review-passes)).
7. **Owner items carry what they unblock**, listed in one place, ordered by what they block, earliest first.
8. **Numbers in the approach.** The approach section states the longest open chain, the tasks at depth 1 and 2, and the files shared by open tasks, from `team.py graph`, before and after any amendment.

## Definition of Ready (before BUILD starts)

- The issue exists, with size, acceptance criteria and out-of-scope, and is claimed by your team (`team:` label, via `scripts/team.py claim`).
- Any spec or plan the size requires is merged; dependencies are merged, or explicitly stubbed.
- Required secrets and data access are available, or the task is scoped to work without them.

## Definition of Done (before MERGE)

- The acceptance criteria are met and **demonstrated**: a command, its output or a screenshot is in the PR.
- New behaviour has tests; a bug fix starts with a failing test. CI is green: lint, format, types, tests, hygiene. No TODOs without a linked issue.
- Docs are updated **inside this PR, written as if it has already merged**: plan checkbox ticked; one fragment (the [command card](teams.md#command-card)'s `fragments.py add`; never an edit to `STATUS.md` or `CHANGELOG.md`) with the STATUS line (at most 240 characters) and the CHANGELOG bullets; ADR status `Accepted`; `.env.example` if config changed. No follow-up PR exists just to record a merge.
- The PR was marked ready by `/ready-pr` (`scripts/ready_pr.py`), which ran the checks, verified the template and the specialist reviews, and waited for CI on the merged commit.
- Data code: every stored fact has `known_at`, and `quant-auditor` has passed. Execution, LLM or secrets code: `safety-reviewer` has passed.

## Domain rules (non-negotiable for TradePartner)

From the research handoff; process rules, not just code rules.

1. **Hypothesis before data.** A strategy test needs a written hypothesis, with an economic rationale, *before* any backtest runs.
2. **Count every trial.** Every backtest run, failures and parameter tweaks included, is logged in the trial registry ([docs/research/trial-registry.md](../research/trial-registry.md) for now, a database table once the backtester exists).
3. **The holdout is sacred.** A locked holdout period is tested once per hypothesis; touching it again means a new registry entry, flagged as such.
4. **Code computes numbers.** LLMs never produce a number that feeds a decision.
5. **LLMs never place orders.** No code path lets model output reach the broker without passing deterministic risk rules; LLM authority beyond that is an ADR decision.
6. **Point-in-time data.** Store *when a fact became known*, not just what it describes. Include delisted securities.
7. **Rules before money.** Risk limits, the kill switch and stop criteria are written down and merged before any paper trading, and again before any live trading.
8. **Log overrides.** Any human override of the system is logged, with the reason.

## Session protocol

The steps are [teams.md, Session protocol](teams.md#session-protocol); every command is on its [command card](teams.md#command-card). No claim, no branch (spikes excepted). In addition:

- **What a writer reads:** the task's plan line and its dependencies' lines (`claim` prints them, `team.py show <Tn>` reprints them) plus the spec sections the line cites, never the whole plan or spec.
- **A blocker or a decision needed** goes on the issue or the PR (a `blocked:` line to the orchestrator, who carries it to the owner), not into `STATUS.md`. Never edit STATUS "Next up" or the frontier snapshot to reserve work.
- **End of session:** commit and push; update the draft PR description; record what changed in one fragment; stopping for good, `team.py release` with a handoff comment on the issue. `STATUS.md` replaces ad-hoc handoff documents: it tells you where things stand in two minutes of reading.

## Retros

At the end of each phase, write `docs/retros/phase-N.md`: what worked, what did not, and one to three concrete changes to these ways-of-working docs, made in the same PR.

## Changes

| Date | Change | Where |
|---|---|---|
| 2026-09-24 | v1.0, from two earlier projects: a research handoff whose 17 open decisions never got owners, and one CLAUDE.md as spec, standards and build order with no git history | PR #2 |
| 2026-09-25 | Docs updated inside the PR; no bookkeeping PRs; fragments | #70, [phase-1 retro](../retros/phase-1.md) |
| 2026-09-30 | Plan shape, after the Phase 4 plan serialised eight windows behind a twelve-PR chain (merges fell from 83 a day to 3); `team.py graph` | #388, #389, #374, #375 |
| 2026-10-01 | One fragment per PR; writers read the claim's lines, not the plan | #351, #352 |
| 2026-10-03, 10-04 | Build → Merge gate named the train, then the two merge classes | [merge-train spec](../specs/merge-train.md), #781 |
| 2026-10-07 | Plan-shape rule 6 files only real defects and owner decisions | #1123 |
| 2026-10-18 | Rewritten for length; lifecycle as a list; merge gate points at rule 7; blockers go on the issue or PR, not into STATUS | #1752 |

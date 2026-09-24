# Development Process

**Status:** Draft v0.1 (pending owner approval in PR #2)

## Why this exists

Lessons from previous projects:

- **Trading research handoff.** The research was excellent, but agents "ran wild". Nothing turned the findings into decisions, and 17 open decisions never got owners.
- **AWT Inventory.** A single CLAUDE.md mixed the spec, standards, UI design and build order. There was no git history, no decision record, and "stop and review" was the only gate.

This process fixes both. **Research serves decisions, decisions feed specs, and specs gate code.** Every stage produces a small, named artifact, so any session (human or agent) can pick up where the last one stopped.

## The lifecycle

```
          ┌────────────── once per project ──────────────┐
          │ 0. CHARTER   why, success/stop criteria, scope │
          └────────────────────────────────────────────────┘
                              │
  per feature / phase:        ▼
  1. RESEARCH (only if a decision is blocked)  → docs/research/YYYY-MM-DD-<slug>.md
  2. DECIDE   (ADR for anything hard to reverse)→ docs/decisions/NNNN-<slug>.md
  3. SPEC     (what and why, acceptance criteria)→ docs/specs/<feature>.md
  4. PLAN     (how: ordered tasks, 1 task = 1 PR)→ docs/plans/<feature>.md
  5. BUILD    (branch, tests first, PR)         → code + tests
  6. REVIEW   (CI + specialist agents + owner)  → merged PR
  7. RECORD   (STATUS, CHANGELOG, plan ticks)   → docs/STATUS.md
                              │
  per phase:                  ▼
  8. RETRO    (what to change in how we work)   → docs/retros/phase-N.md
```

### Gates: what must be true to move forward

| From → To | Gate | Who approves |
|---|---|---|
| Research → Decide | The report answers the brief's question, with evidence grades and a disconfirmation section | Owner |
| Decide → Spec | ADR status is `Accepted` | Owner |
| Spec → Plan | Acceptance criteria are testable, out-of-scope is listed, `spec-critic` has run | Owner |
| Plan → Build | Each task names its files, tests and dependencies. No task over about 400 lines | Owner (can be delegated for size M) |
| Build → Merge | CI is green, PR checklist is complete, specialist reviews are done | Owner merges, or explicitly tells the main session to |
| Phase → next phase | Phase exit criteria are met, retro is written, release is tagged | Owner |

The owner approves by merging the PR that contains the artifact, or by explicitly telling the main session to merge it. Specs, plans and ADRs all land through PRs like code does, so **the approval is recorded in git history.**

## Right-size the ceremony

Not every change needs every document. Size is set on the issue.

| Size | Example | Required |
|---|---|---|
| **S** (under 1 session) | Fix a typo, bump a dep, small bug | Issue → PR |
| **M** (1–3 sessions) | Add a data source, add a report | Issue with spec-lite (acceptance criteria in the issue body) → PR(s) |
| **L** (multi-PR feature or phase) | Backtester, broker integration, LLM layer | Spec + plan docs, and an ADR for any hard-to-reverse choice |

**Always requires an ADR, whatever the size:** choosing a vendor, data source, broker, model, storage format or library that would be painful to swap, plus any change to risk rules or LLM authority.

## Definition of Ready (before BUILD starts)

- [ ] The issue exists, with size, acceptance criteria and out-of-scope.
- [ ] Any spec or plan the size requires is merged.
- [ ] Dependencies are merged, or explicitly stubbed.
- [ ] Required secrets and data access are available, or the task is scoped to work without them.

## Definition of Done (before MERGE)

- [ ] The acceptance criteria are met and **demonstrated**: a command, its output or a screenshot is in the PR.
- [ ] There are tests for new behavior. A bug fix starts with a failing test.
- [ ] CI is green: lint, format, types, tests, hygiene.
- [ ] There are no TODOs without a linked issue.
- [ ] Docs are updated: plan checkbox ticked, `STATUS.md`, `CHANGELOG.md` (`[Unreleased]`), and `.env.example` if config changed.
- [ ] Data code: every stored fact has `known_at`, and `quant-auditor` has passed.
- [ ] Execution, LLM or secrets code: `safety-reviewer` has passed.

## Domain rules (non-negotiable for TradePartner)

These come straight from the research handoff. They are process rules, not just code rules.

1. **Hypothesis before data.** A strategy test needs a written hypothesis, with an economic rationale, *before* any backtest runs.
2. **Count every trial.** Every backtest run, including failures and parameter tweaks, is logged in the trial registry ([docs/research/trial-registry.md](../research/trial-registry.md) for now, a database table once the backtester exists).
3. **The holdout is sacred.** A locked holdout period is tested once per hypothesis. Touching it again means a new registry entry, flagged as such.
4. **Code computes numbers.** LLMs never produce a number that feeds a decision.
5. **LLMs never place orders.** No code path lets model output reach the broker without passing deterministic risk rules. LLM authority beyond that is an ADR decision.
6. **Point-in-time data.** Store *when a fact became known*, not just what it describes. Include delisted securities.
7. **Rules before money.** Risk limits, the kill switch and stop criteria are written down and merged before any paper trading, and again before any live trading.
8. **Log overrides.** Any human override of the system is logged, along with the reason.

## Session protocol (human or agent)

**Start of session:**
1. Read `docs/STATUS.md`.
2. Pick the next unblocked plan task or issue.
3. `git switch main && git pull`, then branch.

**End of session:**
1. Commit and push.
2. Update the draft PR description with the current state.
3. Update `docs/STATUS.md` if anything changed: done, blocked, or a new decision needed.

`STATUS.md` replaces ad-hoc handoff documents. It always tells you where things stand in two minutes of reading.

## Retros

At the end of each phase, write `docs/retros/phase-N.md` covering:
- what worked
- what didn't
- one to three concrete changes to these ways-of-working docs, made in the same PR

This is how the process keeps improving.

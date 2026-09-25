# Teams: many chat windows, one plan

**Status:** Accepted v1.0 (#36, 2026-09-25)

## Why this exists

On 2026-09-25 two orchestrator sessions each read "Next up" in `STATUS.md` and, within one minute of each other, opened issues and PRs for the same two plan tasks (T5: #22/#25, T20: #23/#24). Nothing in the ways of working said *claim before you build*, nothing gave a session an identity, and every PR edited the same three shared files. `STATUS.md` is a snapshot; it cannot arbitrate between concurrent readers.

This document adds the missing layer so that **any number of Claude Code windows** can build from one plan without stepping on each other. It changes nothing about branches, PRs, reviews or who merges: those rules stay in [git-workflow.md](git-workflow.md) and [development-process.md](development-process.md).

## Vocabulary

| Term | Meaning |
|---|---|
| **Team** | One orchestrator chat window plus **one clone** of the repo. Registered once with `scripts/team.py register <name>`. |
| **Claim** | A comment `claim: team:<name>` on a GitHub issue, mirrored by a `team:<name>` label. The claim, not the label, is authoritative. |
| **Plan task** | A checkbox line in `docs/plans/*.md` (`T5`, `T8b`). Its issue carries the label `task:Tn`. |
| **Canonical issue** | The lowest-numbered **open** issue carrying a given `task:Tn` label. |
| **Ready frontier** | Unclaimed plan tasks whose dependencies are all ticked in the plan. |
| **Chain** | Consecutive dependent tasks that one team should keep (listed per plan). |
| **Parked** | Label on a green PR whose team stopped. Re-claim its issue and continue the branch. |

## Set up a team (once per window)

```bash
git clone git@github.com:josejuarez96/tradepartner.git ~/Projects/tradepartner-<name>
cd ~/Projects/tradepartner-<name>
uv sync && uv run pre-commit install
uv run python scripts/team.py register <name>      # writes .team (gitignored), creates label team:<name>
```

- **One clone per team, always.** Two windows in one directory collide on `.claude/worktrees/` and on `.team`.
- Names are short and lowercase (`atlas`, `team-b`). A window that is closed for good keeps its name; the next window may reuse it or pick a new one.
- Everything else (permissions, hooks, agents) comes with the clone from `.claude/settings.json` and `.pre-commit-config.yaml`.

## Session protocol (replaces the generic one for build sessions)

**Start**
1. Read `docs/STATUS.md`.
2. `uv run python scripts/team.py status`: who holds what, the ready frontier, loose issues, parked PRs.
3. `uv run python scripts/team.py claim <Tn | issue#>`. If it says the item is held by another team, pick the next one. **Never** start anyway.
4. Branch as the claim output suggests (`<prefix>/<issue#>-<slug>` from the latest `main`), then work as usual: `implementer` subagents in their own worktrees, tests first, draft PR early.

**During**
- One `implementer` per claimed task. Run several in parallel only on tasks with disjoint files.
- An implementer never claims or releases; it checks that its issue carries the team label and stops if not.
- Anything you notice outside your task becomes an issue (`gh issue create`), unclaimed, for any team to pick up.

**End**
1. Push; update the draft PR description with the current state.
2. If you are stopping for good on an item: `uv run python scripts/team.py release <Tn | issue#>` and a handoff comment on the issue (done, remaining, gotchas). The PR gets the `parked` label if it is green.

## The claim protocol, exactly

1. **GitHub is the source of truth.** Issues, labels and comments decide; `STATUS.md` and the board are snapshots.
2. **A plan task's claim lives on its canonical issue.** `claim T5` finds the lowest-numbered open issue labelled `task:T5`, or creates one from the plan line. If two teams create simultaneously, the higher number is closed as a duplicate by the tool: the tiebreak is deterministic and needs no conversation.
3. **A claim is a comment, replayed in order.** The first unreleased `claim: team:<name>` holds the issue. GitHub orders comments, so there is no tie. The `team:<name>` label mirrors the holder for the board and CI.
4. **Claims are per issue, not per team lifetime.** Release what you stop working on.
5. **Dependencies must be merged.** `claim` refuses a task whose dependencies are unticked. `--allow-unready` exists for stubs that were agreed in writing on the issue (development-process, Definition of Ready).
6. **Owner tasks** (marked `(owner)` in the plan) are never claimed by agents.

## Picking work

In this order:
1. The next task in the chain you are already on. Context carries over and the files are yours already.
2. Any task on the ready frontier.
3. Any unclaimed `size:S` issue (fixes and follow-ups other teams filed).
4. Nothing ready: **do not invent work.** Report to the owner. Parallelism is bounded by the plan's dependency graph, not by the number of windows; another window only helps once the frontier widens.

Chains are a preference, not a lock. Every task is still claimed individually.

## Shared files: how N PRs avoid conflicts

Every PR touches `docs/STATUS.md`, `CHANGELOG.md` and one plan checkbox. To keep rebases trivial:

- **Append one line** to STATUS "Done" and to CHANGELOG `[Unreleased]`. Tick only your checkbox. Do not reorder, rewrite or "tidy" neighbouring lines.
- **Never edit STATUS "Next up" to reserve or advertise work.** The board replaces it. `doc-keeper` refreshes the STATUS snapshot after merges.
- **Rebase on `main` right before marking ready**, and again after any merge that touched those files. Resolve append conflicts by keeping both lines.
- **Code files: only those your plan task names.** If another team's open PR touches one of them, one of you waits; `status` shows open PRs per issue.

## CI guard

The `claims` job runs `scripts/team.py check-claims --pr <n>` on every pull request. It fails when:
- the issue behind the branch has no `team:` label (unclaimed work), or
- the plan task behind the branch has more than one open PR (duplicate work; the lower issue number survives, close the other).

Branches without an issue number (`spike/…`) are skipped with a warning.

## The owner's view

- `uv run python scripts/team.py status` from any clone shows every team's claims.
- **Merge order matters:** merging the head of a chain widens the frontier for everyone. Prefer merging chain heads first.
- Labels of retired teams are harmless; delete them when convenient.
- Two windows on the same task is always a process failure, never a judgment call. When it happens anyway, the lower issue number wins and the other PR is closed with a pointer, as on 2026-09-25 (#34 → #31, #26 → #27).

## Never

- Start from STATUS "Next up" without a claim.
- Share a clone between windows, or run two orchestrators in one directory.
- Touch another team's branch or PR, or close its issue (the duplicate rule is the one exception, and the tool applies it).
- Claim an owner task, or claim past unmerged dependencies without a written stub agreement.
- Merge. The owner merges, or explicitly tells one main session to (git-workflow rule 7).

## Model tiers

Which model a window or agent runs on is set in [agents.md](agents.md#orchestrator-windows-and-model-tiers): Opus 5.5 for orchestrator windows, Fable only where a wrong judgment propagates (specs, plans, ADRs, retros, conflict resolution, these docs), roster models unchanged.

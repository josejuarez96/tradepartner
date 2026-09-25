# Teams: many chat windows, one plan

**Status:** Accepted v1.0 (#36, PR #37, 2026-09-24)

## Why this exists

On 2026-09-24 two orchestrator sessions each read "Next up" in `STATUS.md` and, within one minute of each other, opened issues and PRs for the same two plan tasks (T5: #22/#25, T20: #23/#24). Nothing in the ways of working said *claim before you build*, nothing gave a session an identity, and every PR edited the same three shared files. `STATUS.md` is a snapshot; it cannot arbitrate between concurrent readers.

This document adds the missing layer so that **any number of Claude Code windows** can build from one plan without stepping on each other. It changes nothing about branches, PRs, reviews or who merges: those rules stay in [git-workflow.md](git-workflow.md) and [development-process.md](development-process.md).

## Vocabulary

| Term | Meaning |
|---|---|
| **Team** | One orchestrator session in **its own working directory**: a git worktree of this repo (the default in VS Code) or its own clone. Registered once with `scripts/team.py register <name>`. Jose is not a team; he is the human who merges. The session he types in is a team like any other. |
| **Claim** | A comment `claim: team:<name>` on a GitHub issue, mirrored by a `team:<name>` label. The claim, not the label, is authoritative. |
| **Plan task** | A checkbox line in `docs/plans/*.md` (`T5`, `T8b`). Its issue carries the label `task:Tn`. |
| **Canonical issue** | The lowest-numbered **open** issue carrying a given `task:Tn` label. |
| **Ready frontier** | Unclaimed plan tasks whose dependencies are all ticked in the plan **as merged on `origin/main`**. The tool fetches and reads the plan from there, never from your working tree, so a checkbox ticked inside an unmerged PR does not open the next task. |
| **Chain** | Consecutive dependent tasks that one team should keep (listed per plan). |
| **Parked** | Label on a green PR whose team stopped. Re-claim its issue and continue the branch. |

## Set up a team (once per session)

**In this repo, from a new Claude Code session (VS Code or terminal).** The session first takes a working directory nobody else uses, then registers:

```bash
git worktree add --detach .claude/worktrees/<name> origin/main   # or Claude Code's EnterWorktree tool
cd .claude/worktrees/<name>
uv run python scripts/team.py register <name>      # writes .team here (gitignored), creates label team:<name>
```

The main checkout (`~/Projects/tradepartner`) is a working directory like any other; whichever session registered there owns it. A **separate clone** (`git clone … ~/Projects/tradepartner-<name>`, then `uv sync && uv run pre-commit install && register <name>`) works the same and is only needed for a second VS Code window.

- **One session per working directory, always.** Two sessions in one directory switch branches under each other. `.team` marks whose directory it is.
- Names are short and lowercase (`atlas`, `team-b`). A session that is closed for good keeps its name; the next session may reuse it or pick a new one.
- Implementer subagents get their own worktrees and are told the team name by the orchestrator; they verify the issue's `team:` label and never claim themselves.
- Branch from `origin/main`, not a local `main`: `git fetch origin && git switch -c <branch> origin/main` (the claim output prints this). A worktree cannot check out `main` while the main checkout has it.
- Hooks and permissions are shared through the repo (`.pre-commit-config.yaml`, `.claude/settings.json`).

## Session protocol (replaces the generic one for build sessions)

**Start**
1. Read `docs/STATUS.md`.
2. `uv run python scripts/team.py status`: who holds what, the ready frontier, loose issues, parked PRs.
3. `uv run python scripts/team.py claim <Tn | issue#>`. If it says the item is held by another team, pick the next one. **Never** start anyway.
4. Branch as the claim output suggests (`<prefix>/<issue#>-<slug>` from `origin/main`), then work as usual: `implementer` subagents in their own worktrees, tests first, draft PR early.

**During**
- One `implementer` per claimed task. Run several in parallel only on tasks with disjoint files.
- An implementer never claims or releases; it checks that its issue carries the team label and stops if not.
- Anything you notice outside your task becomes an issue (`gh issue create`), unclaimed, for any team to pick up.

**End**
1. Push; update the draft PR description with the current state.
2. If you are stopping for good on an item: `uv run python scripts/team.py release <Tn | issue#> --park` when the PR is green (the tool labels it `parked`; the next claimant continues the branch after a rebase), or plain `release` when it is red or empty (handoff comment only, the next team may start over). Write the handoff comment on the issue: done, remaining, gotchas.

## The claim protocol, exactly

1. **GitHub is the source of truth.** Issues, labels and comments decide; `STATUS.md` and the board are snapshots.
2. **A plan task's claim lives on its canonical issue.** `claim T5` finds the lowest-numbered open issue labelled `task:T5`, or creates one from the plan line. If two teams create simultaneously, the higher number is closed as a duplicate by the tool: the tiebreak is deterministic and needs no conversation. An issue created by hand with a title like `T5: …` is normalised by `claim <number>`: the tool adds the task label and sends the claim to the canonical issue.
3. **A claim is a comment, replayed in order.** The first unreleased comment whose **entire body** is `claim: team:<name>` holds the issue. GitHub orders comments, so there is no tie. Quoting the line inside a longer comment does nothing. The `team:<name>` label mirrors the holder for the board and CI; when they disagree, the comments win and `claim` repairs the label.
4. **Claims are per issue, not per team lifetime.** Release what you stop working on. A released issue, with its branch and PR, passes to the next team that claims it.
5. **Dependencies must be merged.** `claim` refuses a task whose dependencies are unticked on `origin/main`. `--allow-unready` exists for stubs that were agreed in writing on the issue (development-process, Definition of Ready).
6. **Owner tasks** (marked `(owner)` in the plan, such as T3) need Jose's keys. Only the window Jose is driving claims them, with `--owner-task`. Agents never pass that flag on their own.
7. **A dead window keeps nothing.** If a window stopped without releasing, Jose runs `release <target> --force --reason "…"` from any clone. Agents never use `--force`; the tool cannot tell who is typing, so this is a rule, not a permission.
8. **Spikes are exempt.** A `spike/` branch is never merged, so it needs no claim and the CI guard skips it.

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
- **STATUS's "Ready frontier snapshot" is written only by `doc-keeper`** (and by the PR that changes the process). It is a copy of `status` output, not a place to reserve or advertise work.
- **Rebase on `main` right before marking ready**, and again after any merge that touched those files. Resolve append conflicts by keeping both lines.
- **Code files: only those your plan task names.** If another team's open PR touches one of them, one of you waits; `status` shows open PRs per issue.

## CI guard

The `claims` job runs `scripts/team.py check-claims --pr <n>` on every pull request. It fails when:
- the branch has no issue number (`<prefix>/<issue#>-<slug>`), unless it is a `spike/` or `dependabot/` branch;
- the issue has no claim comment, or its `team:` labels differ from the claim (exactly one label, equal to the holder);
- the issue is titled as a plan task (`T5: …`) but lacks its `task:` label (run `claim <number>` to normalise it);
- another open PR for the same plan task points at a **lower** issue number, or an **older** open PR points at the same issue. The lowest number always survives, so the surviving PR passes and only the duplicate goes red.

A label or comment change on an issue does not re-run a PR's checks. After claiming, push a commit or run `gh run rerun <run-id> --failed`. The job runs the PR's own copy of `scripts/team.py`; that is acceptable for a solo owner who reviews every diff.

## The owner's view

- `uv run python scripts/team.py status` from any clone shows every team's claims.
- **Merge order matters:** merging the head of a chain widens the frontier for everyone. Prefer merging chain heads first.
- Labels of retired teams are harmless; delete them when convenient.
- Two windows on the same task is always a process failure, never a judgment call. When it happens anyway, the lower issue number wins and the other PR is closed with a pointer, as on 2026-09-24 (#34 → #31, #26 → #27).
- You are not a role in the tool. You merge, you decide which window claims T3, and you `release --force` when a window dies. Whatever window you type in is a normal team.

## Never

- Start from STATUS "Next up" without a claim.
- Run two sessions in one working directory.
- Touch a branch, PR or issue that another team currently **holds** (a released or parked one is fair game after you claim it). Closing another team's issue is the tool's job under the duplicate rule, never yours.
- Claim an owner task, or claim past unmerged dependencies without a written stub agreement.
- Merge. The owner merges, or explicitly tells one main session to (git-workflow rule 7).

## Model tiers

Which model a window or agent runs on is set in [agents.md](agents.md#orchestrator-windows-and-model-tiers): Opus 5.5 for orchestrator windows, Fable only where a wrong judgment propagates (specs, plans, ADRs, retros, conflict resolution, these docs), roster models unchanged.

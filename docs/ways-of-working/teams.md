# Teams: many chat windows, one plan

**Status:** Accepted v1.0 (#36, PR #37, 2026-09-24; [changes](#changes) at the end)

Any number of Claude Code windows build from one plan without stepping on each other. This page owns team identity, claims, the ready frontier, the orchestrator, shared files and the CI guard. Branches, PRs and reviews are in [git-workflow.md](git-workflow.md) and [agents.md](agents.md); who merges what is [git-workflow rule 7](git-workflow.md#merge-classes-rule-7).

## Command card

Everything a build window types, in order of use. The rules behind each line follow under [Rules](#rules).

| Command | When |
|---|---|
| `uv run python scripts/team.py start <name>` | Once per new session, where the session opened; then `cd` into the directory it prints and work only there. |
| `uv run python scripts/team.py whoami` | To check which team this directory belongs to. |
| `uv run python scripts/team.py status` | At session start and before picking work: claims, ready frontier, loose issues, parked PRs. |
| `uv run python scripts/team.py claim <Tn\|issue#>` | Before any branch. It prints the branch command, the task's plan line and its dependencies' lines. |
| `uv run python scripts/team.py show <Tn>` | To reprint a task's plan line and dependency lines without claiming. |
| `uv run python scripts/team.py release <Tn\|issue#> [--park]` | When you stop for good on an item: `--park` for a green PR, plain for a red or empty one. Write a handoff comment. |
| `uv run python scripts/fragments.py add <issue> --slug <slug> --status "…" --added "…"` | Once per PR, before ready: the STATUS line and CHANGELOG bullets as one new file (`--changed`, `--fixed`, `--removed` for the other headings). |
| `uv run python scripts/ready_pr.py <pr> --timeout-min 45` (`/ready-pr`) | When the task is done: merges `main` in, runs the checks, waits for CI, marks the PR ready. Never merges. |
| `uv run python scripts/merge_train.py build [PR ...] [--order N [N ...]]` | Only when the owner asks for a train: tests the ready PRs together on `train/<batch id>`, posts a `merge-train:` comment on each, merges nothing. `--resume <batch id>` re-attaches to an unfinished batch. Flags and rules: [the merge train](git-workflow.md#the-merge-train). |
| `uv run python scripts/merge_train.py merge <batch id>` | Only on the owner's "merge train `<batch id>`", by him or the one window he says it to, on the machine that holds the record. `--resume` continues a stopped merge. |
| `uv run python scripts/merge_train.py status [<batch id>]`, `… prune` | A batch's record, or the list; `prune` deletes finished batches' `train/*` branches and worktrees, from the clone that built them. |

Owner only: `team.py release --force`, `team.py claim --owner-task`, `team.py prune --yes`, and the word "merge train `<batch id>`".

## Rules

### Vocabulary

| Term | Meaning |
|---|---|
| **Team** | One session in **its own working directory** (a worktree of this repo or its own clone), registered once with `team.py start <name>` (or `register <name>` in a clone). A chat window Jose types in or a background subagent the orchestrator spawns: either claims, releases and readies its own items, and "window" here means either. Jose is not a team; his word lands class B, the orchestrator lands class A (rule 7). The session he types in is a team like any other. |
| **Claim** | A comment `claim: team:<name>` on a GitHub issue, mirrored by a `team:<name>` label. The claim, not the label, is authoritative. |
| **Plan task** | A checkbox line in `docs/plans/*.md` (`T5`, `T8b`). Its issue carries the label `task:Tn`. |
| **Canonical issue** | The lowest-numbered **open** issue carrying a given `task:Tn` label. |
| **Ready frontier** | Unclaimed plan tasks whose dependencies are all ticked **as merged on `origin/main`**; the tool reads the plan from there, so a tick inside an unmerged PR opens nothing. |
| **Chain** | Consecutive dependent tasks that one team should keep (listed per plan). |
| **Parked** | Label on a green PR whose team stopped. Re-claim its issue and continue the branch. |
| **Orchestrator** | The one session Jose names as orchestrator: the single session above the teams ([Orchestrator](#orchestrator)). |

### Set up a team (once per session)

```bash
uv run python scripts/team.py start <name>
cd <the path it prints>            # ~/Projects/tradepartner-teams/<name>, a worktree outside the repo
```

`start` fetches, creates the directory, writes `.team` there and creates the label; it refuses if the directory exists or the name holds open issues. The name is typed **once**. The main checkout belongs to whichever team registered it (`atlas`); `register` refuses to overwrite another team's `.team`. A separate clone (`git clone … ~/Projects/tradepartner-<name>`, then `uv sync && uv run pre-commit install && register <name>`) works the same and is only needed for a second VS Code window.

- **Your directory is the only directory you touch.** Never `cd` into, read from, or run git in another team's directory or the main checkout, not even "to check"; everything you need is in your worktree, on GitHub, or in `status`. The one exception is the train's record directory, `~/.tradepartner/merge_train/` (`TRADEPARTNER_MERGE_TRAIN_DIR`), outside every team directory so that `build` and `merge` in different windows read the same records; only `scripts/merge_train.py` writes there.
- **One session per working directory, always**; two in one directory switch branches under each other, and `.team` marks whose it is. Names are short and lowercase; a closed session keeps its name, the next may reuse it.
- Branch from `origin/main`, not a local `main` (`git fetch origin && git switch -c <branch> origin/main`; the claim output prints it). A worktree cannot check out `main` while the main checkout has it. Hooks and permissions are shared through the repo.

### Session protocol

For a window Jose drives. A background team follows the brief it was spawned with (its task, the issue, the spec sections its line cites); the rules below still bind it.

**Start.** (1) Read `docs/STATUS.md`, then `uv run python scripts/fragments.py show` for the done entries not folded in yet. (2) `team.py status`. (3) `team.py claim <Tn | issue#>`; held by another team means pick the next one, **never** start anyway. (4) Branch as the claim output says and work: tests first, draft PR early.

**During.**

- **The team is the one writer on its branch** and writes the code itself. The `implementer` agent is optional, for a window that wants a sub-writer in its own worktree; it gets the lines `claim` printed (the plan line and its dependencies'; `team.py show <Tn>` reprints them), not the plan file, it verifies the issue's `team:` label, and it never claims or releases. Several writers only on tasks with disjoint files; what runs alongside is [agents.md](agents.md#parallelism-inside-a-team).
- Reviewers post their full report as the PR's verdict comment and return a ten-line summary; a window never pastes a report into its own context or its messages. Passes: [agents.md](agents.md#review-passes).
- A real defect or an owner decision noticed outside your task becomes an unclaimed issue (`gh issue create`); a nit is dropped.

**Messages between windows.** A message wakes a session that re-reads its whole history to answer, so it is the most expensive thing a window can send.

- A window messages the orchestrator for three things only: `ready #<PR>`, `blocked: <on what, on whom>`, or a finding that affects other teams (main is red, a shared file is in conflict, the disk is full). One complete message, evidence in it.
- **A safety finding is always sent, at once:** an exposed secret (the variable and `file:line`, never the value), a defect on the order path or in the kill switch, a run against the wrong account or endpoint, anything that should stop other windows. Never held for `ready`, never dropped as "progress".
- No acknowledgements, progress reports, "confirm you got this" or thanks; silence means received. A window given an assignment starts it, it does not reply first.
- The orchestrator sends one complete assignment per window per task; before asking a window for its state it reads `team.py status` and the PR. Several notes for one window go in one message; a correction replaces the earlier message in one line. Windows do not message each other: open an issue or tell the orchestrator.

**End.** (1) Push; update the draft PR description. (2) Done: `/ready-pr`; it never merges, and nobody marks a PR ready by hand. (3) Stopping for good: `team.py release <Tn | issue#> --park` when the PR is green (the next claimant continues the branch after bringing `main` in with `/ready-pr`), plain `release` when it is red or empty (the next team may start over); either way a handoff comment on the issue: done, remaining, gotchas. A window done for good says so there; the owner prunes its directory when convenient.

### Orchestrator

- **The session Jose names as the orchestrator.** It spawns the teams, sends each its assignment, triages their `ready` and `blocked` lines and reports to him; it claims and builds like any team when it does so; it is the only session that spawns teams. **Exactly one at a time:** any other session Jose opens takes work as a team and spawns none. ("Orchestrator" in [agents.md](agents.md#orchestrator-windows-and-model-tiers) still means a team's own lead session, the one that spawns that team's agents.)
- **It retires only after the handover is complete:** every live team listed (issue, PR or issue alone, state, model); its live teams stopped, their claims kept for the successor; dead teams' claims released or parked as that team from its own directory, or listed for Jose's `release --force`; and the handover written as a comment on the one standing issue titled exactly **`Orchestrator log`**, never as a memory note (a note may point at the comment, never replace it). Steps and contents: the [handover runbook](../runbooks/orchestrator-handover.md).
- **The successor starts from that comment:** first a one-line "taking over" comment there, then it re-spawns each live team from that team's own directory with "continue PR #N" (or "continue issue #N") and its assignment, never entering it. A team's final report reaches only the session that spawned it, so a team left under a retired orchestrator is a team nobody hears from. Same claim, same branch, same PR.

### The claim protocol, exactly

1. **GitHub is the source of truth.** Issues, labels and comments decide; `STATUS.md` and the board are snapshots.
2. **A plan task's claim lives on its canonical issue.** `claim T5` finds the lowest-numbered open issue labelled `task:T5`, or creates one from the plan line; if two teams create at once, the tool closes the higher number as a duplicate. An issue created by hand with a title like `T5: …` is normalised by `claim <number>`: the tool adds the task label and sends the claim to the canonical issue.
3. **A claim is a comment, replayed in order.** The first unreleased comment whose **entire body** is `claim: team:<name>` holds the issue; GitHub orders comments, so there is no tie, and quoting the line inside a longer comment does nothing. The label mirrors the holder for the board and CI; when they disagree the comments win and `claim` repairs the label.
4. **Claims are per issue, not per team lifetime.** Release what you stop working on; a released issue, with its branch and PR, passes to the next team that claims it.
5. **Dependencies must be merged.** `claim` refuses a task whose dependencies are unticked on `origin/main`. `--allow-unready` is for stubs agreed in writing on the issue ([Definition of Ready](development-process.md#definition-of-ready-before-build-starts)).
6. **Owner tasks** (`(owner)` in the plan) need Jose's keys. Only the window Jose is driving claims them, with `--owner-task`; agents never pass that flag on their own.
7. **A dead window keeps nothing.** Jose runs `release <target> --force --reason "…"` from any clone. Agents never use `--force`: the tool cannot tell who is typing, so this is a rule, not a permission.
8. **Spikes are exempt.** A `spike/` branch is never merged, so it needs no claim and the CI guard skips it.

### Picking work

In this order: (1) the next task in the chain you are on; (2) any task on the ready frontier; (3) any unclaimed issue with a size label, smallest first: `size:S` follow-ups; `type:research` issues whose body is a brief on the [template](../templates/research-brief.md), filed or approved on the issue by Jose (no approved brief, not claimable), run with the `researcher` agent, one issue per run, report PR to `docs/research/`; `type:decision` and `type:docs` issues (ADR, spec or plan drafts), Fable-tier, `spec-critic` before ready, the owner accepts by merging. An issue with no size label is not ready to claim; ask the owner to size it. (4) Nothing left: **do not invent work.** Report to the owner. Parallelism is bounded by the plan's dependency graph, not by the number of windows. Chains are a preference, not a lock: every task is claimed individually.

**The planning team** fills the frontier once a plan is consumed: a team like any other, spawned by the orchestrator when the agent-claimable frontier is below two tasks or three or more unclaimed issues name one shared module, on Fable, one PR in flight, never messaging a team, filing no code issues. Its trigger, inputs, outputs, claim and caps: the [planning-team runbook](../runbooks/planning-team.md).

### Shared files: how N PRs avoid conflicts

Git cannot merge two insertions at one spot, so a PR **adds files, never lines**.

- **Bookkeeping is one fragment per PR** (the command card's `fragments.py add`): `changelog.d/<issue>-<slug>.md` holds the STATUS line (at most 240 characters, linked to the PR or issue) and then the CHANGELOG headings and bullets. PRs opened before #351 may still carry the old pair (`docs/status.d/` plus `changelog.d/`); both are read and folded. Never edit `STATUS.md`'s "Recently done" or `CHANGELOG.md`'s `[Unreleased]` directly; `ready_pr.py` refuses a PR that does (`--allow-shared-files` is for process PRs only). `fragments.py show` prints the pending entries.
- **Folding.** `fragments.py fold` moves every fragment into the two files in issue order and deletes it; STATUS keeps the last 10 "Recently done" lines. `doc-keeper` runs it on the branch it is invoked on: a PR that edits those files for another reason (a plan amendment, a retro, the phase-close PR), or a fold PR **the owner asks for** when STATUS has gone stale, roughly every ten merges. Agents never open a fold PR on their own: a per-merge bookkeeping PR is what the Phase 1 retro banned. `ready_pr.py` recognises a fold (it deletes fragment files); doc-keeper's snapshot refresh in the same PR passes with `--allow-shared-files`.
- **Plan checkboxes.** Tick only your own, collapsed to `- [x] **Tn: Title** (#issue, PR #n) · Files: <paths> · Depends on: <unchanged>` (`tests/test_docs_budget.py` caps a finished line at 400 characters). **STATUS's "Ready frontier snapshot" and "Teams"** are written only by `doc-keeper` (and by the PR that changes the process): copies of `status` output, not a place to reserve work.
- **Bring `main` in with a merge, not a rebase**, right before ready: `ready_pr.py` does it, resolving on its own only two lists of added bullets in the shared files (both kept); anything else stops and tells you. **Code files: only those your plan task names.** If another team's open PR touches one, one of you waits; `status` shows open PRs per issue.

### CI guard

The `claims` job runs `scripts/team.py check-claims --pr <n>` on every pull request. It fails when: the branch has no issue number (`<prefix>/<issue#>-<slug>`), unless it is `spike/` or `dependabot/`; the issue has no claim comment, or its `team:` labels differ from the claim (exactly one label, equal to the holder); the issue is titled as a plan task (`T5: …`) but lacks its `task:` label (`claim <number>` normalises it); or another open PR for the same plan task points at a **lower** issue number, or an **older** open PR points at the same issue (the lowest number survives; only the duplicate goes red). A label or comment change does not re-run a PR's checks: push a commit or `gh run rerun <run-id> --failed`. The job runs the PR's own copy of `scripts/team.py`, acceptable for a solo owner who reviews every diff.

### The owner's view

- `team.py status` from any clone shows every team's claims. Merging the head of a chain widens the frontier for everyone, so prefer chain heads first. Labels of retired teams are harmless.
- **Directories of retired teams are pruned ad hoc, never automatically.** `team.py prune` lists directories with no open claim idle for six hours or more (`--hours`); `--yes` removes them with `git worktree remove`; directories with uncommitted changes are listed and skipped. Agents never run it.
- Two windows on one task is a process failure, never a judgment call; when it happens, the lower issue number wins and the other PR is closed with a pointer.
- You are not a role in the tool. You approve every class-B merge (your word on that PR, or your "merge train `<batch id>`", which lets the window you say it to run `merge_train.py merge`); the orchestrator lands class A under the same [rule 7](git-workflow.md#merge-classes-rule-7). You decide which window claims an owner task, and you `release --force` when a window dies. Whatever window you type in is a normal team.

### Never

- Start from STATUS "Next up" without a claim.
- Run two sessions in one working directory, or register in a directory that already has a different `.team`.
- Enter another team's directory or the main checkout for any reason.
- Touch a branch, PR or issue another team currently **holds** (released or parked is fair game after you claim it). Closing another team's issue is the tool's job under the duplicate rule, never yours.
- Claim an owner task, or claim past unmerged dependencies without a written stub agreement.
- Merge. The one exception is the orchestrator landing a class-A PR under every condition of [rule 7](git-workflow.md#merge-classes-rule-7); a class-B PR lands only on the owner's word for that PR, a train only on his "merge train `<batch id>`" (building one is not merging and happens only when he asks); the ruleset bypass (`--admin`) is never a window's.

### Model tiers

Set by the table in [agents.md](agents.md#orchestrator-windows-and-model-tiers), named by the orchestrator in the spawn; this page does not restate the rows.

## Changes

| Date | Change | Where |
|---|---|---|
| 2026-09-24 | v1.0: claims, team identity, the CI guard, after two sessions opened PRs for the same two tasks within a minute | #36, PR #37 |
| 2026-09-25 to 10-01 | Fragments instead of shared-file lines (#70); one fragment per PR and the finished-line shape (#351, #353); report-only messages and two review passes (#489); `claim` prints the task lines (#352); `prune` skips dirty directories (#167) | those issues |
| 2026-10-04 | Orchestrator section and the planning team (audit items 1 and 6); background subagents count as windows; train commands on the card | #780, #782, #773, #529, [audit](../retros/2026-10-04-orchestration-layer-audit.md) |
| 2026-10-18 | Rewritten for length; merge rules live only in git-workflow.md; the team writes its own code, `implementer` optional; protocol scoped to driven windows; handover and planning-team procedures moved to runbooks | #1752 |

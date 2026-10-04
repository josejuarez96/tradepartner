# Teams: many chat windows, one plan

**Status:** Accepted v1.0 (#36, PR #37, 2026-09-24)

## Command card

Everything a build window types, in order of use. The rules behind each line follow under [Rules and history](#rules-and-history).

| Command | When |
|---|---|
| `uv run python scripts/team.py start <name>` | Once per new session, where the session opened; then `cd` into the directory it prints and work only there. |
| `uv run python scripts/team.py whoami` | To check which team this directory belongs to. |
| `uv run python scripts/team.py status` | At session start and before picking work: claims, ready frontier, loose issues, parked PRs. |
| `uv run python scripts/team.py claim <Tn\|issue#>` | Before any branch. It prints the branch command, the task's plan line and its dependencies' lines. |
| `uv run python scripts/team.py show <Tn>` | To reprint a task's plan line and dependency lines without claiming. |
| `uv run python scripts/team.py release <Tn\|issue#> [--park]` | When you stop for good on an item: `--park` for a green PR, plain for a red or empty one. Write a handoff comment. |
| `uv run python scripts/fragments.py add <issue> --slug <slug> --status "…" --added "…"` | Once per PR, before ready: the STATUS line and CHANGELOG bullets as one new file. |
| `uv run python scripts/ready_pr.py <pr> --timeout-min 45` (`/ready-pr`) | When the task is done: merges `main` in, runs the checks, waits for CI, marks the PR ready. Never merges. |
| `uv run python scripts/merge_train.py build [PR ...] [--order N [N ...]]` | Only when the owner asks for a train, by any window or the orchestrator: tests the ready PRs together on `train/<batch id>`, posts a `merge-train:` comment on each. Merges nothing. `--resume <batch id>` re-attaches to an unfinished batch (after an inconclusive run). Flags in full: [git-workflow.md](git-workflow.md#the-merge-train). |
| `uv run python scripts/merge_train.py merge <batch id>` | Only on the owner's "merge train `<batch id>`", by him or the one window he says it to, on the machine that holds the record: lands the batch's longest green prefix, nothing else. This exact spelling is the one `.claude/settings.json` prompts on (#529). `--resume` continues a stopped merge. |
| `uv run python scripts/merge_train.py status [<batch id>]` | A batch's record, or the list of records. `uv run python scripts/merge_train.py prune` (no flags) deletes finished batches' `train/*` branches and worktrees; run it from the clone that built them. |

Owner only: `team.py release --force`, `team.py claim --owner-task`, `team.py prune --yes`, and the word "merge train `<batch id>`".

## Rules and history

### Why this exists

On 2026-09-24 two orchestrator sessions each read "Next up" in `STATUS.md` and, within one minute of each other, opened issues and PRs for the same two plan tasks (T5: #22/#25, T20: #23/#24). Nothing in the ways of working said *claim before you build*, nothing gave a session an identity, and every PR edited the same three shared files. `STATUS.md` is a snapshot; it cannot arbitrate between concurrent readers.

This document adds the missing layer so that **any number of Claude Code windows** can build from one plan without stepping on each other. It changes nothing about branches, PRs, reviews or who merges: those rules stay in [git-workflow.md](git-workflow.md) and [development-process.md](development-process.md).

### Vocabulary

| Term | Meaning |
|---|---|
| **Team** | One orchestrator session in **its own working directory**: a git worktree of this repo (the default in VS Code) or its own clone. Registered once with `scripts/team.py register <name>`. Jose is not a team; he is the human who approves every merge ([git-workflow rule 7](git-workflow.md): his "merge train `<batch id>`", or "merge" on one specific PR). The session he types in is a team like any other. |
| **Claim** | A comment `claim: team:<name>` on a GitHub issue, mirrored by a `team:<name>` label. The claim, not the label, is authoritative. |
| **Plan task** | A checkbox line in `docs/plans/*.md` (`T5`, `T8b`). Its issue carries the label `task:Tn`. |
| **Canonical issue** | The lowest-numbered **open** issue carrying a given `task:Tn` label. |
| **Ready frontier** | Unclaimed plan tasks whose dependencies are all ticked in the plan **as merged on `origin/main`**. The tool fetches and reads the plan from there, never from your working tree, so a checkbox ticked inside an unmerged PR does not open the next task. |
| **Chain** | Consecutive dependent tasks that one team should keep (listed per plan). |
| **Parked** | Label on a green PR whose team stopped. Re-claim its issue and continue the branch. |

### Set up a team (once per session)

**From a new Claude Code session in this repo (VS Code or terminal), one command:**

```bash
uv run python scripts/team.py start <name>
cd <the path it prints>            # ~/Projects/tradepartner-teams/<name>, a worktree outside the repo
```

`start` fetches, creates the directory, writes `.team` there and creates the label. It refuses if the directory exists or if the name holds open issues (a live session is using it). The team name is typed **once**, in that command. The main checkout belongs to whichever team registered it (`atlas`), and `register` refuses to overwrite another team's `.team`.

Team directories live **outside the repo** on purpose: a session that lists files in its own directory never sees another team's work. A **separate clone** (`git clone … ~/Projects/tradepartner-<name>`, then `uv sync && uv run pre-commit install && register <name>`) works the same and is only needed for a second VS Code window.

- **Your directory is the only directory you touch.** Never `cd` into, read from, or run git in another team's directory or in the main checkout, not even "to check". Everything you need is in your worktree, on GitHub, or in `status`. The one exception is the merge train's record directory, `~/.tradepartner/merge_train/` (`TRADEPARTNER_MERGE_TRAIN_DIR`), which sits outside every team directory on purpose so that a `build` in one window and the `merge` in another, or in the owner's shell, read the same records; only `scripts/merge_train.py` writes there, never your hands.
- **One session per working directory, always.** Two sessions in one directory switch branches under each other. `.team` marks whose directory it is.
- Names are short and lowercase (`atlas`, `team-b`). A session that is closed for good keeps its name; the next session may reuse it or pick a new one.
- Implementer subagents get their own worktrees and are told the team name by the orchestrator; they verify the issue's `team:` label and never claim themselves.
- Branch from `origin/main`, not a local `main`: `git fetch origin && git switch -c <branch> origin/main` (the claim output prints this). A worktree cannot check out `main` while the main checkout has it.
- Hooks and permissions are shared through the repo (`.pre-commit-config.yaml`, `.claude/settings.json`).

### Session protocol (replaces the generic one for build sessions)

**Start**
1. Read `docs/STATUS.md`, then `uv run python scripts/fragments.py show` for the recently done entries not folded in yet.
2. `uv run python scripts/team.py status`: who holds what, the ready frontier, loose issues, parked PRs.
3. `uv run python scripts/team.py claim <Tn | issue#>`. If it says the item is held by another team, pick the next one. **Never** start anyway.
4. Branch as the claim output suggests (`<prefix>/<issue#>-<slug>` from `origin/main`), then work as usual: `implementer` subagents in their own worktrees, tests first, draft PR early. Hand the implementer the lines `claim` printed (the task's plan line and its dependencies' lines; `team.py show <Tn>` reprints them), not the plan file (#352).

**During**
- One `implementer` per claimed task, one writer per branch. Run several in parallel only on tasks with disjoint files. Read-only helpers and the reviewers may run alongside; the table in [agents.md](agents.md#parallelism-inside-a-team) says what goes in parallel and what does not.
- Reviewers (`spec-critic`, `quant-auditor`, `safety-reviewer`) post their full report as the PR's verdict comment and return a ten-line summary; a window never pastes a report into its own context or its messages (#352).
- **Two review passes per reviewer per PR** (#489): pass 1 is the review; pass 2 verifies the fixes and reads everything pushed to the PR's own files since pass 1 (commits a merge of `main` brought in are skipped). An unfixed finding, a BLOCKER, or a new SHOULD FIX on the order path fails it; what else it notices goes into one follow-up `size:S` issue, not a third pass. The rule and its one exception are in [agents.md](agents.md#review-passes).
- An implementer never claims or releases; it checks that its issue carries the team label and stops if not.
- Anything you notice outside your task becomes an issue (`gh issue create`), unclaimed, for any team to pick up.

**Messages between windows** (#489)

Every message wakes the session that receives it, and that session re-reads its whole history to answer. Measured on 2026-10-01, 97% of all tokens were such re-reads, so a message is the most expensive thing a window can send.
- A window messages the orchestrator for three things only: `ready #<PR>`, `blocked: <on what, on whom>`, or a finding that affects other teams (main is red, a shared file is in conflict, the disk is full). One message, complete, with the evidence in it.
- **A safety finding is always sent, at once:** an exposed secret (name the variable and the `file:line`, never the value), a defect on the order path or in the kill switch, a run against the wrong account or endpoint, anything that should stop other windows. It is never held for the `ready` message and never dropped as "progress".
- No acknowledgements, no progress reports, no "confirm you got this", no thanks. Silence means received. A window that was given an assignment starts it; it does not reply first.
- The orchestrator sends one assignment per window per task and puts everything the window needs in it. Before asking a window for its state it reads `uv run python scripts/team.py status` and the PR; it asks only for what those cannot show.
- Several notes for one window go in one message. A correction replaces the earlier message in one line; it does not ask for a confirmation either.
- Windows do not message each other. A window with something for another team opens an issue or tells the orchestrator.

**End**
1. Push; update the draft PR description with the current state.
1. Done with the task: `/ready-pr` (runs `uv run python scripts/ready_pr.py <pr>`): merges `main` in, runs the checks, verifies the template and the specialist reviews, waits for CI on that commit and marks the PR ready. It never merges. Do not mark a PR ready by hand.
2. If you are stopping for good on an item: `uv run python scripts/team.py release <Tn | issue#> --park` when the PR is green (the tool labels it `parked`; the next claimant continues the branch after bringing `main` in with `/ready-pr`), or plain `release` when it is red or empty (handoff comment only, the next team may start over). Write the handoff comment on the issue: done, remaining, gotchas. If the window is done for good, say so there; the owner prunes its directory when convenient.

### The claim protocol, exactly

1. **GitHub is the source of truth.** Issues, labels and comments decide; `STATUS.md` and the board are snapshots.
2. **A plan task's claim lives on its canonical issue.** `claim T5` finds the lowest-numbered open issue labelled `task:T5`, or creates one from the plan line. If two teams create simultaneously, the higher number is closed as a duplicate by the tool: the tiebreak is deterministic and needs no conversation. An issue created by hand with a title like `T5: …` is normalised by `claim <number>`: the tool adds the task label and sends the claim to the canonical issue.
3. **A claim is a comment, replayed in order.** The first unreleased comment whose **entire body** is `claim: team:<name>` holds the issue. GitHub orders comments, so there is no tie. Quoting the line inside a longer comment does nothing. The `team:<name>` label mirrors the holder for the board and CI; when they disagree, the comments win and `claim` repairs the label.
4. **Claims are per issue, not per team lifetime.** Release what you stop working on. A released issue, with its branch and PR, passes to the next team that claims it.
5. **Dependencies must be merged.** `claim` refuses a task whose dependencies are unticked on `origin/main`. `--allow-unready` exists for stubs that were agreed in writing on the issue (development-process, Definition of Ready).
6. **Owner tasks** (marked `(owner)` in the plan, such as T3) need Jose's keys. Only the window Jose is driving claims them, with `--owner-task`. Agents never pass that flag on their own.
7. **A dead window keeps nothing.** If a window stopped without releasing, Jose runs `release <target> --force --reason "…"` from any clone. Agents never use `--force`; the tool cannot tell who is typing, so this is a rule, not a permission.
8. **Spikes are exempt.** A `spike/` branch is never merged, so it needs no claim and the CI guard skips it.

### Picking work

In this order:
1. The next task in the chain you are already on. Context carries over and the files are yours already.
2. Any task on the ready frontier.
3. Any unclaimed issue with a size label, smallest first. That includes:
   - `size:S` fixes and follow-ups other teams filed;
   - `type:research` issues whose body is a brief following the [research brief template](../templates/research-brief.md), filed or approved on the issue by Jose (no approved brief, not claimable): claim one, run the `researcher` agent on it (Opus), one issue per run, report PR to `docs/research/`;
   - `type:decision` and `type:docs` issues (ADR, spec or plan drafts): Fable-tier per agents.md, `spec-critic` before ready, owner accepts by merging.
   An issue with no size label is not ready to claim; ask the owner to size it.
4. Nothing left: **do not invent work.** Report to the owner; the orchestrator may then spawn the planning team (below). Parallelism is bounded by the plan's dependency graph, not by the number of windows; another window only helps once the frontier widens.

Chains are a preference, not a lock. Every task is still claimed individually.

**The planning team.** Added 2026-10-04 (#782, [orchestration layer audit](../retros/2026-10-04-orchestration-layer-audit.md) item 6). Once a plan is consumed the frontier has no owner: on 2026-10-04 the agent-claimable frontier was zero, 36 unclaimed `size:S` follow-ups filled the gap, and none of the roadmap's idle-capacity items had an issue. The planning team is that duty on a trigger: a team like any other, spawned by the orchestrator, not a standing architect agent and not a new agent file.
- **Trigger.** The orchestrator spawns it when the agent-claimable frontier is below two tasks, or when three or more unclaimed issues name one shared module (`run.py`, `window.py`, `wrapper.py`, `resume.py`) in the title or the body (`gh issue list --search "run.py in:title,body"`). Agent-claimable means the `agent` tasks on the ready frontier of `status` whose plan line carries no owner gate; `status` marks only `owner` against `agent`, so the gate is read from each frontier task's line (`team.py show Tn`).
- **Model.** Fable, by the tier table's spec, plan and ADR drafting row ([agents.md](agents.md#orchestrator-windows-and-model-tiers)).
- **Inputs.** [roadmap.md](../roadmap.md), the plans, `uv run python scripts/team.py graph`, the open issues, STATUS "Decisions needed from owner".
- **Outputs, one PR at a time.** (a) The next item of roadmap.md "Calendar-bound phases" (the Phase 5 spec and plan, Phase 6 preparation, the `data-validator` and `journal-analyst` agents), as a spec or plan PR; or a plan amendment that chains the shared-module issues: each issue becomes a plan task line (`Files:` and `Depends on:` on the line, the issue titled or labelled as that task), at most three to a chain per module, declared in the plan's lanes paragraph (plan-shape rules 1 and 3), with the rest folded into one `size:S` issue per module (rule 6), so that `graph` shows the contention and `claim` refuses the next until the one before is merged. (b) A decision memo per open owner question, as one comment on that question's issue, lettered options and a recommendation; a "Decisions needed" line that has no issue gets one `type:decision` issue first. Its PRs go through `spec-critic` before ready, as item 3 above says; the owner accepts by merging.
- **Claim.** A roadmap item has no issue until the planning team files one `type:docs` issue for it and claims it with `team.py claim` before branching, like any team; the chaining amendment and a decision issue are claimed the same way.
- **Caps.** It never messages a team; teams read its output at claim time, from the plan line and the issue. One PR in flight. Never more than one phase beyond the highest phase in build on STATUS's phase line (Phase 5 while Phase 4 is in build), except that Phase 6 preparation, the owner's own docs tasks, may get the decision issues and memos it needs, never a spec or plan. It stops when three spec, plan, ADR or process PRs are waiting for the owner's word, its own or anyone's. It files no code issues.

### Shared files: how N PRs avoid conflicts

Until 2026-09-25 every PR appended one line to `docs/STATUS.md` ("Done") and one to `CHANGELOG.md` (`[Unreleased]`) at the same anchor. Git cannot merge two insertions at one spot, so every merge to `main` conflicted every other open PR, and each team looped: merge main, resolve, push, wait for CI, main moves, repeat (#70). The fix is that a PR **adds files, never lines**:

- **Bookkeeping is one fragment per PR.** `uv run python scripts/fragments.py add <issue> --slug <slug> --status "<Recently done line>" --added "<CHANGELOG bullet>"` writes `changelog.d/<issue>-<slug>.md`: the STATUS line (at most 240 characters, linked to the PR or issue) first, then the CHANGELOG headings and bullets (#351). New files never conflict. PRs opened before #351 may still carry the old pair (`docs/status.d/` plus `changelog.d/`); both are still read and folded. Do not edit `STATUS.md`'s "Recently done" list or `CHANGELOG.md`'s `[Unreleased]` directly; `ready_pr.py` refuses a PR that does (`--allow-shared-files` is for process PRs only). Reading STATUS: `uv run python scripts/fragments.py show` prints the pending entries.
- **Folding.** `uv run python scripts/fragments.py fold` moves every fragment into the two files in issue order and deletes it; STATUS keeps only the last 10 "Recently done" lines, because CHANGELOG and git history keep the rest (#351). `doc-keeper` runs it on the branch it is invoked on: a PR that edits those files for another reason (a plan amendment, a retro, the phase-close PR), or a fold PR **the owner asks for** when STATUS has gone stale, roughly every ten merges. Agents never open a fold PR on their own: a per-merge bookkeeping PR is what the Phase 1 retro banned. `ready_pr.py` recognises a fold (it deletes fragment files) and lets it through; doc-keeper's snapshot refresh in the same PR passes with `--allow-shared-files`.
- **Plan checkboxes.** Tick only your checkbox, and collapse that line to `- [x] **Tn: Title** (#issue, PR #n) · Files: <paths> · Depends on: <unchanged>` (#353; `tests/test_docs_budget.py` caps a finished line at 400 characters). Different lines merge cleanly, and the CI `claims` job already stops two PRs from building one task.
- **STATUS's "Ready frontier snapshot" and "Teams" are written only by `doc-keeper`** (and by the PR that changes the process). They are copies of `status` output, not a place to reserve or advertise work.
- **Bring `main` in with a merge, not a rebase**, right before marking ready: `ready_pr.py` does it. A merge needs no force-push, so a branch two teams have pushed stays safe, and the squash merge flattens it anyway. The only conflict it resolves on its own is two lists of added bullets in the shared files (both sides kept); anything else stops and tells you.
- **Code files: only those your plan task names.** If another team's open PR touches one of them, one of you waits; `status` shows open PRs per issue.

### CI guard

The `claims` job runs `scripts/team.py check-claims --pr <n>` on every pull request. It fails when:
- the branch has no issue number (`<prefix>/<issue#>-<slug>`), unless it is a `spike/` or `dependabot/` branch;
- the issue has no claim comment, or its `team:` labels differ from the claim (exactly one label, equal to the holder);
- the issue is titled as a plan task (`T5: …`) but lacks its `task:` label (run `claim <number>` to normalise it);
- another open PR for the same plan task points at a **lower** issue number, or an **older** open PR points at the same issue. The lowest number always survives, so the surviving PR passes and only the duplicate goes red.

A label or comment change on an issue does not re-run a PR's checks. After claiming, push a commit or run `gh run rerun <run-id> --failed`. The job runs the PR's own copy of `scripts/team.py`; that is acceptable for a solo owner who reviews every diff.

### The owner's view

- `uv run python scripts/team.py status` from any clone shows every team's claims.
- **Merge order matters:** merging the head of a chain widens the frontier for everyone. Prefer merging chain heads first.
- Labels of retired teams are harmless; delete them when convenient.
- **Directories of retired teams are pruned ad hoc, never automatically.** A team may take another claim later, so its directory stays while it is touched. When the disk or the board looks cluttered: `uv run python scripts/team.py prune` prints which directories have no open claim and have been idle for six hours or more (`--hours` to change); `--yes` removes them with `git worktree remove` and prunes the worktree list. Directories with uncommitted changes are listed and skipped, never removed (#167). Agents never run it.
- Two windows on the same task is always a process failure, never a judgment call. When it happens anyway, the lower issue number wins and the other PR is closed with a pointer, as on 2026-09-24 (#34 → #31, #26 → #27).
- You are not a role in the tool. You approve every merge (your "merge train `<batch id>`" lets the window you say it to run `merge_train.py merge`; git-workflow rule 7), you decide which window claims T3, and you `release --force` when a window dies. Whatever window you type in is a normal team.

### Never

- Start from STATUS "Next up" without a claim.
- Run two sessions in one working directory, or register in a directory that already has a different `.team`.
- Enter another team's directory or the main checkout for any reason.
- Touch a branch, PR or issue that another team currently **holds** (a released or parked one is fair game after you claim it). Closing another team's issue is the tool's job under the duplicate rule, never yours.
- Claim an owner task, or claim past unmerged dependencies without a written stub agreement.
- Merge. The merge train is the normal way a PR lands: the owner says "merge train `<batch id>`" and runs `merge_train.py merge`, or tells one window to (git-workflow rule 7). Building a train is not merging and happens only when he asks. A hand `gh pr merge` of one green PR happens only on his word for that specific PR, by him or the window he tells; the ruleset bypass (`--admin`) is never a window's.

### Model tiers

Which model a window or agent runs on is set by the table in [agents.md](agents.md#orchestrator-windows-and-model-tiers) (#489); the orchestrator names it in the assignment. This page does not restate the rows, so the table is the only place to read or change them.

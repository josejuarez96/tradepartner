# Git Workflow

**Status:** Accepted v1.0 (PR #2, 2026-09-24; [changes](#changes) at the end). Trunk-based development, short-lived branches, every change through a PR; `main` is always green and runnable. This page is the one home for the merge classes and the merge train; CLAUDE.md, the agent files, [teams.md](teams.md) and [development-process.md](development-process.md) link here and do not restate them.

## The rules in one screen

1. **Never commit directly to `main`.** Owner, agents, docs, typos: everyone.
2. **One issue, one branch, one PR.** No issue: create one first (a size-S issue can be one line).
3. **Branch from the latest `main`; merge back within about 3 days.** Long-lived branches drift into conflicts.
4. **Push whenever you stop working.** The remote branch is the backup and the handoff point.
5. **Open a draft PR early**, after the first meaningful commit, so CI runs and progress is visible.
6. **Squash-merge only.** The PR title becomes the one commit on `main`, so it must be a Conventional Commit.
7. **Every merge is approved: a class-A PR by this rule, a class-B PR by the owner's word on that PR** ([Merge classes](#merge-classes-rule-7)). Agents open PRs and address review comments; they never force-push shared branches or push to `main`. This written rule replaces the standing approval that was granted in chat and re-granted to each new orchestrator session. It holds until the owner edits it, and no session asks for it again ([ADR 0002, amendment 2026-10-04](../decisions/0002-git-workflow.md#amendment-2026-10-04-two-merge-classes-781-audit-item-2)).

## Merge classes (rule 7)

Which class a PR is in depends only on the files it changes. One class-B file makes the whole PR class B.

### Class A: the orchestrator lands it

- **Files:** all under `src/`, `tests/`, `docs/research/`, `docs/retros/`, `docs/runbooks/` or `changelog.d/`. A `docs/plans/` edit that only ticks the PR's own task box and collapses that one line to its finished shape (`- [x] **Tn: …** (#issue, PR #pr) · Files: … · Depends on: …`) keeps the PR in class A; any other plan edit makes it class B.
- **Conditions, every one of them:** it is ready, through `ready_pr.py`; `checks` and `claims` are green on its head SHA; the latest verdict of every required reviewer is `PASS`; every review thread on it is resolved, whoever opened it; and the orchestrator has trial-merged it against the current `main` (the branch merged onto `origin/main` in a scratch worktree, the targeted tests run) and posted one line on the PR naming the `main` SHA it was tried at.
- **Who and how:** the orchestrator session (the one session the owner is typing to, [teams.md](teams.md#orchestrator)) lands it, and nobody else; team windows and subagents never merge. Until the merge train is the class-A path, class A lands by hand: `gh pr merge --squash --match-head-commit <head SHA>`, with `checks` green on that head. That is what "no bypass" means here: GitHub applies the admin bypass by itself to a red one ([Both classes](#both-classes)).
- **The train becomes the class-A path** only once audit item 3's conditions hold (#764 fixed, its PR #765 merged, one dry build green on a real batch) and the [merge-train spec](../specs/merge-train.md#owner-decisions-2026-10-01) is amended to say so; that is the owner's decision. Until then [The merge train](#the-merge-train) stands as written: a train is built only when the owner asks for one, and `merge_train.py merge <batch id>` runs only on his "merge train `<batch id>`", for that id, by him or the window he said it to.

### A plan-named release PR lands like class A

The release PR of an owner release task (an `(owner)` plan line whose steps say "merge this task's release PR") is landed by the orchestrator inside the sitting, without a separate owner word, when it changes only what that plan line's Files name for the release step (the switch defaults and version constants it names, the tests those values break, and the PR's own fragment), it is ready, `checks` is green on its head and every required reviewer's latest verdict is `PASS`. The orchestrator removes its `hold` label at that step of the sitting, and the PR comment says so. Any other file in it, or a value the line does not name, makes it class B.

### Class B: it waits for the owner's word on that PR

- **Files:** every other PR, one that changes any file outside the class-A directories. That includes everything under `docs/specs/`, `docs/plans/`, `docs/decisions/`, `docs/ways-of-working/`, `.claude/` and `.github/`, and `CLAUDE.md`, `docs/charter.md`, `docs/roadmap.md`, `.pre-commit-config.yaml`, `scripts/ready_pr.py`, `scripts/merge_train.py`, `scripts/team.py`, `pyproject.toml` and `uv.lock`. Also any PR labelled `hold`; the label is checked at the trial merge and again just before the merge command.
- **How:** it is merged only when the owner says to merge that specific PR (by hand, `gh pr merge --squash`, no bypass, CI green on its head, its review threads resolved) or names it in a "merge train `<batch id>`". Nothing in the checks, the labels or the ready flag stands in for his word.

### Both classes

- The ruleset's pull-request bypass (`gh pr merge --admin`) is the owner's alone, in his own shell, for a red-check emergency. No window ever runs it.
- **The server does not enforce the classes, or the green-head condition, against our tokens.** Every token in this repository is the owner's and admin, so the required check binds none of them (#762, a throwaway PR red on purpose, merged through the bypass on a plain `gh pr merge`; #772 reverts it). The class rule is a process rule, kept by this page, the `ask` prompt on `gh pr merge` in `.claude/settings.json`, and the trial-merge line on each class-A PR, not by the server.

## Branches, commits, pushes

| Situation | Prefix |
|---|---|
| New capability from a spec/plan task | `feat/` |
| Bug fix, including urgent ones | `fix/` |
| Docs, specs, plans, ADRs, research reports | `docs/` or `research/` |
| Tooling, deps, CI | `chore/` |
| Restructure with no behaviour change | `refactor/` |
| Throwaway experiment, **never merged** | `spike/` |

- Every situation above gets a branch, named `<prefix>/<issue#>-<short-slug>` (`feat/14-price-ingestion`). **Split it** past about 400 changed lines (lockfiles and generated files excluded), when the description needs "and also…", or when it touches two unrelated areas. **Spikes** are disposable: findings go to `docs/research/` in a separate `docs/` PR, then the branch is deleted.
- **Commit** after each logical step that leaves the tests passing; small commits bisect and review better and are squashed anyway. Never commit secrets, data files (`data/` is gitignored), notebooks with outputs, or commented-out code.
- **Messages** are [Conventional Commits](https://www.conventionalcommits.org), `type(scope): imperative summary`. Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`, `perf`, `research`. Scopes grow as modules appear: `data`, `backtest`, `signals`, `risk`, `exec`, `llm`, `journal`, `infra`. Agent commits end with a `Co-Authored-By:` trailer.
- **Push** at the end of every work session (keep the PR a draft), before asking for review, before switching work. Never `--force` on a branch another person or agent works on; `--force-with-lease` on your own branch after a rebase is fine.

## PR lifecycle

`issue → branch → draft PR → CI green → self-review → specialist review agents → ready for review → merge (rule 7) → branch auto-deleted`

**Before marking a PR ready**, run `/ready-pr` (`uv run python scripts/ready_pr.py <pr>`): it checks the list below the same way for every team, waits for CI on the exact commit, and marks the PR ready. Marking ready by hand is not the process.

- CI is green (`checks`) and the `claims` job passes: the branch's issue carries your `team:` label and no other open PR builds the same plan task ([CI guard](teams.md#ci-guard)).
- The PR template is filled in (what/why, `Closes #n`, how it was verified) and the author has reviewed their own diff on GitHub.
- The required specialist reviews have run ([agents.md](agents.md#review-passes)): `quant-auditor` for data, backtest or signal changes; `safety-reviewer` for broker, orders, LLM inputs or secrets.
- Docs are updated: one STATUS/CHANGELOG fragment (the [command card](teams.md#command-card)'s `fragments.py add`, never the shared files themselves), the plan checkbox, `.env.example`, an ADR if a decision was made.

**Keeping current.** `ready_pr.py` merges `origin/main` into the branch; merging, not rebasing, needs no force-push, which matters once more than one team has pushed the branch, and `main` stays linear because the PR is squash-merged. A rebase is fine on a branch only you have pushed. **No stacked PRs:** a PR based on another open PR's branch is closed or broken when the base squash-merges; wait for the base, then branch from `main`. **CI must have run on the exact commit being merged:** "no checks reported" is not green; after any merge of `main`, rebase or force-push, wait for the run (`gh pr checks <n> --watch`; `ready_pr.py` does this for you).

### The merge train

A ready PR's CI tested it against that run's `main`, not against the other ready PRs. The train ([spec](../specs/merge-train.md)) tests a batch of ready PRs **together** and lands only what ran green. The flags are written out here only ([command card](teams.md#command-card) points here); the `merge` spelling is the one `.claude/settings.json` prompts on (#529).

```bash
uv run python scripts/merge_train.py build [PR ...] [--order N [N ...]] [--timeout-min 60] [--poll-s 30]
uv run python scripts/merge_train.py build --resume <batch id>   # re-attach to a batch's branch and run
uv run python scripts/merge_train.py merge <batch id> [--resume] # only on the owner's "merge train <batch id>"
uv run python scripts/merge_train.py status [<batch id>]         # print one record, or list every record
uv run python scripts/merge_train.py prune                       # delete train/* branches and worktrees of finished batches
```

- **`build`** runs only when the owner asks for a train (never on a schedule or because N PRs are ready), by any window or the orchestrator, from any team directory; it merges nothing. With no PR numbers it takes every **eligible** PR: open and not a draft; the owner's own, from this repository, against `main`; not `parked`; not the culprit of an earlier train at this head; `checks` and `claims` green on its head SHA; no unticked template box; a `Closes #` for its branch's issue; its fragment in the diff, with a CHANGELOG bullet on a `feat/` or `fix/` branch; every required review verdict `PASS`; no unresolved review thread, whoever opened it. These are the `ready_pr.py` checks plus the train's own, re-derived from GitHub at build time rather than read from the ready flag. `--order` sets the sequence (ascending PR number by default).
- It squash-merges the batch in order onto `origin/main` in its own worktree, pushes `train/<batch id>`, waits for the one full CI run (`ci.yml` runs the whole suite on `train/**`), reruns a red run once so a flake names no culprit, bisects a confirmed red over prefixes, and posts one comment per PR whose first line is `merge-train: <OUTCOME> batch <batch id>` (`TESTED`, `DROPPED`, `CULPRIT`, `HELD`, `INCONCLUSIVE`, `INELIGIBLE`). A PR that conflicts inside the train is `DROPPED`, for its team to bring `main` in with `ready_pr.py`; the next train takes it. A PR merely behind `main` needs no new `ready_pr.py` run: testing it against the current `main` is the train's job.
- **`merge`** takes a batch id and nothing else: no PR list, no order, no flag that widens the batch. It re-verifies that `origin/main` is still the batch's base and every PR still matches the record, lands the longest **green** prefix PR by PR (`gh pr merge --squash --match-head-commit`, the landed tree checked against the recorded one), posts `MERGED` or `HELD` (`UNCONFIRMED` on a PR that landed but failed the tree or parent check), and stops at the first PR it cannot land; `--resume` continues from the recorded position. It runs on the machine that holds the batch's record: the window that ran `build`, or the owner's shell on the same machine.
- **`status`** and **`prune`** are housekeeping; `prune` runs from the clone whose `build` created the worktree. The record lives in `~/.tradepartner/merge_train/` (`TRADEPARTNER_MERGE_TRAIN_DIR`), outside every team directory; the PR comments and that record are the whole log. The owner's "merge train `<batch id>`" is the only instruction that runs `merge`, so a window never asks "shall I merge?": it reports `build`'s result and waits.

## Releases

Tag `v0.<phase>.0` on `main` when a phase completes (`v0.2.0` = data foundation done). The PR that closes the phase folds the pending fragments (`uv run python scripts/fragments.py fold`) and moves the `[Unreleased]` entries in `CHANGELOG.md` under the new version.

## Parallel agents and teams

Each orchestrator session is a **team** in its own working directory (a worktree or a clone); claims, the ready frontier, shared files and the CI guard are in [teams.md](teams.md), and no branch is cut without `uv run python scripts/team.py claim` (`spike/` excepted). Within a team, parallel subagents get their own worktree and branch each, never a shared working directory, and work on non-overlapping files ([agents.md](agents.md#parallelism-inside-a-team)).

## How the rules are enforced

| Layer | What it blocks | Where |
|---|---|---|
| pre-commit and pre-push hooks | Commits and pushes on `main`; private keys and secrets (gitleaks); files over 500 KB | `.pre-commit-config.yaml` |
| Claude Code permissions | Pushing to `main`, force-pushing, reading `.env` (deny); `gh pr merge` and `uv run python scripts/merge_train.py merge` always prompt (ask) | `.claude/settings.json` |
| GitHub repo settings | Merge commits and rebase-merge (squash only); stale branches (auto-delete) | Repo settings |
| CI | Lint, format, types, hygiene on every PR; the full suite, sharded, when the PR touches code, tests, scripts, deps or CI, and on every push to `main` and `train/**`. PR CI keeps the full suite (#773): targeted-only PR CI waits until `ready_pr.py` or the ruleset checks that a hand-merged head ran the full suite | `.github/workflows/ci.yml` |
| CI `claims` job | A PR whose issue is unclaimed; two open PRs for one plan task | `scripts/team.py check-claims` |
| Merge train | Ready PRs landing on a combined tree CI never ran | [above](#the-merge-train) |
| Server-side ruleset `protect-main`, active since 2026-10-04 (plan T75b, id 24443727) | Direct pushes, force-push and deletion, for every token, the owner's included; a PR required, squash only, the `checks` run required on the head commit. It does **not** refuse a red-`checks` merge by the owner's admin token ([Both classes](#both-classes)). What the file says and why, and how the owner changes it: [runbook](../runbooks/protect-main-ruleset.md) | `.github/rulesets/protect-main.json` |

**Local hooks**, once per clone (worktrees share them): `uv run pre-commit install`. **Emergency override:** `git commit --no-verify` exists; using it on `main` requires a note in the PR or `STATUS.md` explaining why, and agents may never use it.

## Changes

| Date | Change | Where |
|---|---|---|
| 2026-09-24 | v1.0 | PR #2 |
| 2026-09-30 | No stacked PRs; CI on the exact commit | [phase-1 retro](../retros/phase-1.md) |
| 2026-10-03 | Merge train, after two `main`-red incidents on 10-01 | [spec](../specs/merge-train.md), #529 |
| 2026-10-04 | Rule 7 as two classes, replacing the chat-granted approval (#781, [audit](../retros/2026-10-04-orchestration-layer-audit.md) item 2); PR CI keeps the full suite (#773); ruleset active; #762 red-merge test (#772); threads resolved whoever opened them (#803) | [ADR 0002](../decisions/0002-git-workflow.md#amendment-2026-10-04-two-merge-classes-781-audit-item-2) |
| 2026-10-06, 10-13 | The PR's own plan-box tick keeps class A (#1110); a plan-named release PR lands like class A (#1647) | those PRs |
| 2026-10-18 | Rewritten for length, meaning unchanged; the one home for the classes and the train; ruleset explanation moved to a runbook | #1752 |

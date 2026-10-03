# Git Workflow

**Status:** Accepted v1.0 (PR #2, 2026-09-24)
**Model:** trunk-based development with short-lived branches, and every change goes through a PR. `main` is always green and runnable.

## The rules in one screen

1. **Never commit directly to `main`.** This applies to everyone: owner, agents, docs, typos.
2. **One issue → one branch → one PR.** If there is no issue, create one first. Size-S issues can be a single line.
3. **Branch from the latest `main`, merge back within about 3 days.** Long-lived branches drift and turn into merge conflicts.
4. **Push whenever you stop working.** The remote branch is the backup and the handoff point for the next session or agent.
5. **Open a draft PR early** (after the first meaningful commit), so CI runs and progress is visible.
6. **Squash-merge only.** The PR title becomes the single commit on `main`, so it must be a Conventional Commit.
7. **The owner approves every merge.** Agents open PRs and address review comments. They never force-push shared branches or push to `main`. PRs land through the **merge train** ([The merge train](#the-merge-train)): any window or the orchestrator may build and test a train when the owner asks for one, and nothing else starts one; **only the owner's "merge train `<batch id>`", the id required, is the instruction to merge**, it covers exactly the PRs that batch's record names, and the owner or the window he said it to runs `merge_train.py merge <batch id>`. A single PR is merged by hand (`gh pr merge --squash`, no bypass: a green PR passes the ruleset as it is) only when the owner explicitly says to merge that specific PR and CI is green on its head. The ruleset's pull-request bypass (`gh pr merge --admin`) is the owner's alone, in his own shell, for a red-check emergency; no window ever runs it. Subagents never merge. (Amended 2026-10-03 by [ADR 0002](../decisions/0002-git-workflow.md#amendment-2026-10-03-merge-train-460-plan-t75), owner decisions 2 and 5 of the [merge-train spec](../specs/merge-train.md#owner-decisions-2026-10-01).)

## When to create a branch

| Situation | Branch? | Prefix |
|---|---|---|
| New capability from a spec/plan task | Yes | `feat/` |
| Bug fix, including urgent ones | Yes | `fix/` |
| Docs, specs, plans, ADRs, research reports | Yes | `docs/` or `research/` |
| Tooling, deps, CI | Yes | `chore/` |
| Restructure with no behavior change | Yes | `refactor/` |
| Throwaway experiment ("does this library even work?") | Yes, **never merged** | `spike/` |

**Name format:** `<prefix>/<issue#>-<short-slug>`, for example `feat/14-price-ingestion` or `research/9-insider-disconfirmation`.

**Split the branch** when:
- the diff exceeds about 400 changed lines (excluding lockfiles and generated files)
- you find yourself writing "and also…" in the PR description
- the work touches two unrelated areas

**Spikes:** the code on a `spike/` branch is disposable. Findings are written up in `docs/research/` in a separate `docs/` PR. Then the spike branch is deleted.

## When to commit

- **Commit** after each logical step that leaves the tests passing, such as "add parser" or "add test for holiday edge case". Small commits make bisecting and reviewing easier. They get squashed on merge anyway.
- **Don't commit** secrets, data files (`data/` is gitignored), notebooks with outputs, or commented-out code.

**Messages** follow [Conventional Commits](https://www.conventionalcommits.org): `type(scope): imperative summary`.
- **Types:** `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`, `perf`, `research`.
- **Scopes** (grow as modules appear): `data`, `backtest`, `signals`, `risk`, `exec`, `llm`, `journal`, `infra`.
- **Agent commits** end with a `Co-Authored-By:` trailer.

## When to push

- At the end of every work session, even if the work is incomplete. Keep the PR as a draft.
- Before asking for review.
- Before switching to other work.
- Never with `--force` on a branch someone else (or another agent) is working on. `--force-with-lease` on your own branch after a rebase is fine.

## PR lifecycle

```
issue → branch → draft PR → CI green → self-review → specialist review agents → ready for review → merge train (build, then the owner's "merge train <batch id>") → branch auto-deleted
```

**Before marking a PR ready for review:** run `/ready-pr` (`uv run python scripts/ready_pr.py <pr>`). It checks every item below the same way for every team, waits for CI on the exact commit, and marks the PR ready. Marking ready by hand is not the process.
- [ ] CI is green (`checks` job) and the `claims` job passes: the branch's issue carries your `team:` label and no other open PR builds the same plan task ([teams.md](teams.md)).
- [ ] The PR template is filled in: what/why, linked issue (`Closes #n`), how it was verified.
- [ ] The author has reviewed their own diff on GitHub.
- [ ] The required specialist reviews have run (see [agents.md](agents.md)):
  - `quant-auditor` for data, backtest or signal changes
  - `safety-reviewer` for broker, orders, LLM inputs or secrets
- [ ] Docs are updated: a STATUS/CHANGELOG fragment (`scripts/fragments.py add`, never the shared files themselves), plan checkboxes, `.env.example`, and an ADR if a decision was made.

**Keeping current:** `ready_pr.py` merges `origin/main` into the branch (`git merge origin/main`). Merging, not rebasing, means no force-push, which matters once a branch has been pushed by more than one team. `main` keeps its linear history regardless, because the PR is squash-merged. A rebase is still fine on a branch only you have pushed.

**No stacked PRs.** Don't open a PR whose base is another open PR's branch: when the base squash-merges, GitHub closes or breaks the stacked PR. Wait for the base to merge, then branch from `main`. (Phase 1 retro.)

**CI must have run on the exact commit being merged.** "No checks reported" is not green; wait for the run (`gh pr checks <n> --watch`) after any merge of `main`, rebase or force-push; `ready_pr.py` does this wait for you. (Phase 1 retro.)

### The merge train

A ready PR is not merged on its own run: its CI tested it against the `main` of that run, not against the other ready PRs. The train ([spec](../specs/merge-train.md), added 2026-10-03 after two `main`-red incidents on 2026-10-01) tests a batch of ready PRs **together** and lands only what ran green. `scripts/merge_train.py` has four commands (`build` from any team directory; `merge` and `prune` where the next paragraphs say); this block is the one place the flags are written out, [teams.md](teams.md#command-card) points here, and the `merge` spelling is the one `.claude/settings.json` prompts on (#529):

```bash
uv run python scripts/merge_train.py build [PR ...] [--order N [N ...]] [--timeout-min 60] [--poll-s 30]
uv run python scripts/merge_train.py build --resume <batch id>   # re-attach to a batch's branch and run
uv run python scripts/merge_train.py merge <batch id> [--resume] # only on the owner's "merge train <batch id>"
uv run python scripts/merge_train.py status [<batch id>]         # print one record, or list every record
uv run python scripts/merge_train.py prune                       # delete train/* branches and worktrees of finished batches
```

- **`build`** runs only when the owner asks for a train (never on a schedule or because N PRs are ready), by any window or the orchestrator. With no PR numbers it takes every eligible PR (open and not a draft, the owner's own from this repository, against `main`, not `parked`, not the culprit of an earlier train at this head, `checks` and `claims` green on its head SHA, no unticked template box, a `Closes #`, its fragment in the diff, and every required review verdict `PASS`: the same checks `ready_pr.py` runs, re-derived from GitHub at build time rather than read from the ready flag); `--order` sets the sequence (ascending PR number by default). It squash-merges the batch in order onto `origin/main` in its own worktree, pushes `train/<batch id>`, waits for the one full CI run (`ci.yml` runs the whole suite on `train/**`), reruns a red run once so a flake names no culprit, bisects a confirmed red over prefixes, and posts one comment per PR whose first line is `merge-train: <OUTCOME> batch <batch id>` (`TESTED`, `DROPPED`, `CULPRIT`, `HELD`, `INCONCLUSIVE`, `INELIGIBLE`). It merges nothing. A PR that conflicts inside the train is `DROPPED` for its team to bring `main` in with `ready_pr.py`; the next train takes it. A PR that is merely behind `main` needs no new `ready_pr.py` run: testing it against the current `main` is the train's job.
- **`merge`** takes a batch id and nothing else: no PR list, no order, no flag that widens the batch. It re-verifies that `origin/main` is still the batch's base and every PR still matches the record, lands the longest **green** prefix PR by PR (`gh pr merge --squash --match-head-commit`, the landed tree checked against the recorded one), posts `MERGED` or `HELD`, and stops at the first PR it cannot land. `--resume` continues a stopped merge from the recorded position. It runs on the machine that holds the batch's record: the window that ran `build`, or the owner's shell on the same machine.
- **`status`** and **`prune`** are housekeeping; `prune` runs from the clone whose `build` created the worktree (a worktree is registered in the clone that made it). Until #642 lands, not while a `build` is running in that clone: today `prune` would remove the live worktree before its record exists.

The record lives in `~/.tradepartner/merge_train/` (`TRADEPARTNER_MERGE_TRAIN_DIR`), outside every team directory; the PR comments and that record are the whole log. The owner's "merge train `<batch id>`" is the only merge instruction, so a window never asks "shall I merge?": it reports `build`'s result and waits.

## Releases

- Tag `v0.<phase>.0` on `main` when a phase completes, for example `v0.2.0` = data foundation done.
- Fold the pending fragments (`uv run python scripts/fragments.py fold`) and move the `[Unreleased]` entries in `CHANGELOG.md` under the new version in the same PR that closes the phase.

## Parallel agents and teams

- Each orchestrator session is a **team** in its **own working directory** (a worktree of this repo, or a clone). Claims, the ready frontier, shared-file rules and the CI guard are in [teams.md](teams.md). No branch without `uv run python scripts/team.py claim` (`spike/` branches excepted).
- Within a team, each subagent working in parallel gets its own **git worktree** and branch (`claude --worktree` or the worktree isolation option). Two agents never share a working directory.
- Parallel agents must work on **non-overlapping files**. If two plan tasks touch the same module, run them one after the other. What may run alongside a single implementer (read-only helpers, reviewers) is in [agents.md](agents.md#parallelism-inside-a-team).

## How the rules are enforced

The repository is **public since 2026-10-01**, so GitHub's repository rulesets are available without a paid plan; once the owner activates it (plan T75b), the server-side layer below is the one layer that does not depend on a hook being installed or a permission rule being read. Enforcement is layered:

| Layer | What it blocks | Where |
|---|---|---|
| pre-commit hook | Commits on `main`, private keys and secrets (gitleaks), files over 500 KB | `.pre-commit-config.yaml` |
| pre-push hook | Pushes to `main` | `.pre-commit-config.yaml` (`no-push-to-main`) |
| Claude Code permission rules | Agents pushing to main, force-pushing, reading `.env` (deny); `gh pr merge` and `uv run python scripts/merge_train.py merge` always prompt for confirmation (ask) | `.claude/settings.json` |
| GitHub repo settings | Merge commits and rebase-merge (squash only), stale branches (auto-delete) | Repo settings (applied) |
| CI | Lint, format, types, hygiene on every PR; tests when the PR touches code, tests, scripts, deps or CI; the full suite on every push to `main` and to `train/**` | `.github/workflows/ci.yml` |
| CI `claims` job | A PR whose issue is unclaimed; two open PRs for one plan task | `scripts/team.py check-claims`, `.github/workflows/ci.yml` |
| **Merge train** | Ready PRs landing on a combined tree CI never ran: `build` tests the batch as one tree, bisects a red one, and `merge` lands only trees that ran green, only on the owner's "merge train `<batch id>`" | `scripts/merge_train.py` ([The merge train](#the-merge-train)) |
| **Server-side ruleset** (direct pushes, force-push and deletion refused for every token, the owner's included; a PR required, squash only, the `checks` run required on the head commit) | **Activated by the owner (plan T75b)**; until then the layers above are the whole guard | `.github/rulesets/protect-main.json` |

**What the ruleset file says and why** (JSON allows no comment, so the explanation lives here; spec req 10, owner decision 1 of 2026-10-01):
- `strict_required_status_checks_policy: false`. Strict would require every branch to be up to date with `main` before it merges, which is exactly what the train does not require: the train tests a PR against the current `main` as part of the batch, so a PR behind `main` is eligible as it stands.
- `bypass_actors`: the repository admin role (`actor_type: RepositoryRole`, `actor_id: 5`) with `bypass_mode: pull_request`, **never `always`**. GitHub's rulesets API reference requires an `actor_id` for `RepositoryRole` but no longer prints the numbers; the base repository role ids are maintain 2, write 4, admin 5 (the Terraform GitHub provider's `repository_ruleset` docs list them for this field), and the read-back after activation below is the check. A green PR needs no bypass. The bypass exists so the owner, in his own shell, can merge a PR whose `checks` run is red in an emergency, as an explicit act (the bypass dialog in the UI, or `gh pr merge --admin`), while a direct push to `main`, a force-push and a deletion stay refused for his token too. `OrganizationAdmin` does not apply to a personal repository.
- No `merge-train` status context. A context only the train would set would refuse every hand merge and turn a `main`-red hotfix into a one-PR train. The ruleset is a guard against habit, not an authentication boundary: every token in this repository is the owner's, and what keeps an agent from using the bypass is rule 7 and the `ask` entry, not the server.

**Activating the server-side ruleset** (owner, once; plan T75b; `gh api -X POST` is not in an agent's allow list):

```bash
gh api -X POST repos/josejuarez96/tradepartner/rulesets --input .github/rulesets/protect-main.json
```

Then read it back, `gh api repos/josejuarez96/tradepartner/rulesets/<id> --jq '.bypass_actors, .rules[-1]'` with the id from `gh api repos/josejuarez96/tradepartner/rulesets`: `bypass_actors` must show `RepositoryRole` 5 with `pull_request`, and the status-check rule `strict_required_status_checks_policy: false`. A later change to the file is applied with `gh api -X PUT repos/josejuarez96/tradepartner/rulesets/<id> --input .github/rulesets/protect-main.json`; the file in the repository is the record of what is active.

**Local hooks after cloning** (run once per clone; worktrees share the hooks):

```bash
uv run pre-commit install
```

**Emergency override:** `git commit --no-verify` exists. Using it on `main` requires a note in the PR or `STATUS.md` explaining why. Agents may never use `--no-verify`.

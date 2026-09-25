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
7. **The owner approves every merge.** Agents open PRs and address review comments. They never force-push shared branches or push to `main`. The main session may run the squash merge **only when the owner explicitly tells it to merge that specific PR** and CI is green. Subagents never merge.

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
issue → branch → draft PR → CI green → self-review → specialist review agents → ready for review → owner merges (or tells the agent to) → branch auto-deleted
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

## Releases

- Tag `v0.<phase>.0` on `main` when a phase completes, for example `v0.2.0` = data foundation done.
- Fold the pending fragments (`uv run python scripts/fragments.py fold`) and move the `[Unreleased]` entries in `CHANGELOG.md` under the new version in the same PR that closes the phase.

## Parallel agents and teams

- Each orchestrator session is a **team** in its **own working directory** (a worktree of this repo, or a clone). Claims, the ready frontier, shared-file rules and the CI guard are in [teams.md](teams.md). No branch without `uv run python scripts/team.py claim` (`spike/` branches excepted).
- Within a team, each subagent working in parallel gets its own **git worktree** and branch (`claude --worktree` or the worktree isolation option). Two agents never share a working directory.
- Parallel agents must work on **non-overlapping files**. If two plan tasks touch the same module, run them one after the other. What may run alongside a single implementer (read-only helpers, reviewers) is in [agents.md](agents.md#parallelism-inside-a-team).

## How the rules are enforced

This repo is private on GitHub Free, which **cannot enforce branch protection server-side**. Enforcement is therefore layered:

| Layer | What it blocks | Where |
|---|---|---|
| pre-commit hook | Commits on `main`, private keys and secrets (gitleaks), files over 500 KB | `.pre-commit-config.yaml` |
| pre-push hook | Pushes to `main` | `.pre-commit-config.yaml` (`no-push-to-main`) |
| Claude Code permission rules | Agents pushing to main, force-pushing, reading `.env` (deny); `gh pr merge` always prompts for confirmation (ask) | `.claude/settings.json` |
| GitHub repo settings | Merge commits and rebase-merge (squash only), stale branches (auto-delete) | Repo settings (applied) |
| CI | Lint, format, types, hygiene on every PR; tests when the PR touches code, tests, scripts, deps or CI, and on every push to main | `.github/workflows/ci.yml` |
| CI `claims` job | A PR whose issue is unclaimed; two open PRs for one plan task | `scripts/team.py check-claims`, `.github/workflows/ci.yml` |
| **Server-side ruleset** (blocks direct pushes, force-push, deletion; requires PR + green CI) | **Inactive until GitHub Pro** | `.github/rulesets/protect-main.json` |

**Activating the server-side ruleset** after upgrading to GitHub Pro:

```bash
gh api -X POST repos/josejuarez96/tradepartner/rulesets --input .github/rulesets/protect-main.json
```

**Local hooks after cloning** (run once per clone; worktrees share the hooks):

```bash
uv run pre-commit install
```

**Emergency override:** `git commit --no-verify` exists. Using it on `main` requires a note in the PR or `STATUS.md` explaining why. Agents may never use `--no-verify`.

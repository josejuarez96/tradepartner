# 0002. Trunk-based git workflow; PR for every change; owner approves every merge

**Status:** Accepted  ·  **Date:** 2026-09-24  ·  **Issue:** #1

## Context
There is one human (the owner) and several AI agents committing. Agents can produce a lot of change quickly. The owner needs a single review point and a clean history. The repo is private on GitHub Free, so server-side branch protection is not available.

## Options considered
1. **Trunk-based + short-lived branches + squash merge**: simple, and a linear history where one commit equals one reviewed change.
2. **GitFlow (develop/release branches)**: built for release trains with multiple versions. Too much ceremony for this project.
3. **Commit directly to main**: fast, but no review gate for agent output and no CI gate.

## Decision
Option 1, as specified in [git-workflow.md](../ways-of-working/git-workflow.md). Merges need the owner's explicit approval: the owner merges, or tells the main session to merge a specific PR (subagents never merge). Protection is enforced locally (pre-commit and pre-push hooks, Claude deny rules). A server-side ruleset is ready in `.github/rulesets/protect-main.json` for when the plan allows it.

## Consequences
- Good: every change on `main` has an issue, a PR, CI, and an owner-approved merge.
- Bad: small changes pay a PR overhead. This is accepted, because PRs are cheap with `gh`. Local-only protection can be bypassed with `--no-verify` or by a clone without hooks installed.
- Reversibility: cheap.
- Revisit if: another human joins (add required reviews and CODEOWNERS), or on upgrading to GitHub Pro (activate the ruleset).

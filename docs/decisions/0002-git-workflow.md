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

## Amendment 2026-10-03: merge train (#460, plan T75)

The decision stands. Two sentences of it change meaning, after the owner's decisions of 2026-10-01 on the [merge-train spec](../specs/merge-train.md#owner-decisions-2026-10-01); the sections above are left as written, and this note is appended rather than edited in.

1. **"Tells the main session to merge a specific PR" becomes "merge a specific batch whose PR list is frozen in the train's record".** PRs land through the merge train: `scripts/merge_train.py build` tests a batch of ready PRs together as one tree, posts the result on each PR and freezes the batch (the ordered PR list, head SHAs, trees and green prefixes) in a local record; `scripts/merge_train.py merge <batch id>` lands the longest green prefix and nothing else, and refuses a record that does not match the comments `build` posted. Any window or the orchestrator may build a train when the owner asks for one, and nothing builds one unasked (decision 5); only the owner's **"merge train `<batch id>`"**, the id required, is the instruction to merge, and it covers exactly the PRs the record names (decision 2). A single PR may still be merged by hand, on the owner's word for that specific PR with CI green on its head; a green PR needs no bypass, and the ruleset's pull-request-only bypass is the owner's alone, for a red-check emergency. Subagents never merge, as before.
2. **"The repo is private on GitHub Free, so server-side branch protection is not available" no longer holds.** The repository is public since 2026-10-01, so rulesets are available. `.github/rulesets/protect-main.json` is activated by the owner (plan T75b) with non-strict required checks (the train tests a PR against the current `main`; the branch need not be up to date), the repository admin role as a bypass actor for pull requests only (never for a direct push, a force-push or a deletion), and no train-only status context (decision 1). The explanation of each choice is in [git-workflow.md](../ways-of-working/git-workflow.md#how-the-rules-are-enforced).

Consequences, in addition to the ones above:
- Good: a batch of individually green PRs is tested as the tree it will produce before it lands, so the 2026-10-01 incidents (#430 after #387; #444 × #429, in the spec's Problem section) cannot recur through the train. Once the ruleset is active (T75b), direct pushes to `main` are refused server-side, for every clone, with or without hooks.
- Bad: a train run costs about 30 minutes of wall clock per batch and per probe, and a dropped or culprit PR waits for the next train. A hand merge remains possible for the owner and is still untested against the other ready PRs.
- Revisit if: GitHub's native merge queue becomes available to this repository (it would close the seconds-wide race the spec's req 6 names). The "upgrading to GitHub Pro" trigger above is retired by this amendment: the ruleset is activated on the public repository instead.

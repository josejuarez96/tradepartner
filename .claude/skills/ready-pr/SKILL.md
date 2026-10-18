---
name: ready-pr
description: Bring your draft PR up to date with main, run every check and required review, and mark it ready for the owner, in one deterministic command. Use when your task is done and the PR is a draft, or after the owner asks you to bring main in. Never merges.
---

# Ready a PR

Run this when the work on your claimed issue is done and the draft PR describes it.

## Before you run it
1. **One fragment, not the shared files:** `uv run python scripts/fragments.py add <issue> --slug <slug> --status "<Recently done line, ≤240 chars, with the PR number>" --added "<CHANGELOG bullet>"` (or `--changed` / `--fixed`). Never edit `docs/STATUS.md` or `CHANGELOG.md`. Tick only your own plan box.
2. **Reviews:** the plan line's `Review:`, plus `quant-auditor` for data, store, signals, backtests and `safety-reviewer` for broker, orders, secrets, config, LLM inputs. `QUANT_PREFIXES` / `SAFETY_PREFIXES` in `ready_pr.py` are the minimum it enforces by path. Give each the PR number; it posts its full report as a PR comment whose first line is the verdict (`quant-auditor: PASS`) and returns a ten-line summary. If it could not post, post its file with `gh pr comment <n> --body-file <path>`. Only a comment's first line counts, the latest verdict per agent wins, and only `PASS` passes: `PASS WITH FIXES` and `FAIL` block until a re-review after the fixes posts `PASS` (#356). The PR body never counts (#352).
3. **Template:** tick every box, or mark it `n/a` with why. The body says `Closes #<issue>`.

## Run it
```bash
uv run python scripts/ready_pr.py <pr-number> --timeout-min 45
```
From your team directory, on the PR's branch, clean tree. `--timeout-min 45`: CI with tests outlasts the 25-minute default. It:
1. merges `origin/main` in (no rebase, no force-push); a STATUS/CHANGELOG conflict is auto-resolved only when both sides purely inserted bullets at one spot (both kept); a deleted or edited line stops it;
2. runs the local checks, then verifies the template and the reviews;
3. pushes, waits for CI on that exact commit, and marks the PR ready.

| Flag | Effect |
|---|---|
| `--dry-run` | everything up to the push (the merge of `main` is still committed locally) |
| `--tests` / `--full-tests` / `--no-tests` | force local targeted pytest / run the full suite locally / skip it |
| `--allow-shared-files` | see Never |

Always run locally: ruff check, ruff format --check, mypy, the fragment check, `tests/test_docs_budget.py`. No local pytest by default: CI runs the full suite on every diff touching `TEST_TRIGGER_PREFIXES` / `TEST_TRIGGER_FILES` and on every push to main. `--tests` runs the test files the diff maps to, or the full suite when the mapping is unclear, and prints which (rules: `targeted_tests` in `ready_pr.py`, #456).

## When it says NOT READY
Each message names one fix. Fix, commit, run again.

| Message | Do |
|---|---|
| `conflicts in <file> ... resolve by hand` | Keep both sides where both are right, `git add`, `git commit`. Never drop the other team's lines. |
| `adds lines to the shared lists` | Move those lines into your fragment and remove **only those lines** from STATUS/CHANGELOG; keep other edits there (Blocked, Decisions needed, snapshot sections in a process PR). |
| `no fragment for this PR's issue` | Step 1. |
| `local check failed` | Fix lint, types or tests. Never skip tests. |
| `unticked boxes` / `Closes #` | `gh pr edit <n> --body-file <file>`. |
| `run these reviews` | Run the named agent, fix, post the verdict (step 2). |
| `CI failure` | Read `gh pr checks <n>`, fix, run again. |
| `CI timeout` | Run again; CI queues can be slow. |

## Never
- Pass `--allow-shared-files`, unless the PR is a fold host the owner asked for (doc-keeper folding fragments and refreshing the STATUS snapshot sections) or a process change that must edit those files.
- Merge. Landing follows [git-workflow rule 7](../../../docs/ways-of-working/git-workflow.md#the-rules-in-one-screen); subagents and team windows never merge, and nobody uses `--admin`.
- Force-push.
- Run it in another team's directory or the main checkout.

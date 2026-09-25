---
name: ready-pr
description: Bring your draft PR up to date with main, run every check and required review, and mark it ready for the owner, in one deterministic command. Use when your task is done and the PR is a draft, or after the owner asks you to bring main in. Never merges.
---

# Ready a PR

Run this when the work on your claimed issue is complete and the draft PR describes it.

## Before you run it

1. Bookkeeping goes in **fragments, not the shared files**. If you have not done so:
   ```bash
   uv run python scripts/fragments.py add <issue> --slug <short-slug> \
     --status "<one Done line, with the PR number>" \
     --added "<one CHANGELOG bullet>"      # or --changed / --fixed
   ```
   Do not edit `docs/STATUS.md` or `CHANGELOG.md` yourself. Tick only your plan checkbox.
2. Run the specialist reviews the touched paths require (the plan task's `Review:` field):
   `quant-auditor` for data, store, signals, backtests; `safety-reviewer` for broker, orders,
   secrets, config, LLM inputs. Address the findings, then post **a PR comment** whose first
   line is the verdict: `quant-auditor: PASS` or `quant-auditor: PASS WITH FIXES`, followed by
   the findings and what you did. The command looks for that line in comments only; the PR
   body does not count because the template already names both agents.
3. Fill in the PR template. Tick every box, or replace an inapplicable one with `n/a` and why.
   The body must say `Closes #<issue>`.

## Run it

```bash
uv run python scripts/ready_pr.py <pr-number>
```

From your team directory, with the PR's branch checked out and a clean tree. It:
merges `origin/main` in (no rebase, no force-push), auto-resolves a STATUS/CHANGELOG
conflict only when both sides purely inserted bullets at the same spot (keeps both; a deleted
or edited line stops it), runs ruff, mypy, the fragment check and pytest,
verifies the template and reviews, pushes, waits for CI on that exact commit, then marks the
PR ready. Expect the wait: CI takes a few minutes. `--dry-run` runs everything up to the
push; it still commits the merge of `main` locally, which is what you want anyway.

## When it says NOT READY

Each message names one fix. Do that fix, commit, and run the command again:

| Message | Do |
|---|---|
| `conflicts in <file> ... resolve by hand` | Open the file, keep both sides where both are right, `git add`, `git commit`, run again. Never resolve by dropping the other team's lines. |
| `adds lines to the shared lists` | Move those lines into fragments (step 1 above) and remove **only those lines** from `STATUS.md` / `CHANGELOG.md`. Keep any other edit you made there (Blocked, Decisions needed, snapshot sections in a process PR). |
| `no fragment for this PR's issue` | Step 1 above. |
| `local check failed` | Fix lint, types or tests. Do not skip tests. |
| `unticked boxes` / `Closes #` | Edit the PR body: `gh pr edit <n> --body-file <file>`. |
| `run these reviews` | Spawn the named agent on your diff, address findings, post the verdict comment (step 2 above). |
| `CI failure` | Read `gh pr checks <n>`; fix; run again. |
| `CI timeout` | Run again; CI queues can be slow. |

## Never

- Pass `--allow-shared-files` unless your PR is a fold host the owner asked for (doc-keeper folding fragments and refreshing the STATUS snapshot sections) or a process change that must edit those files.
- Merge the PR. The owner merges, or tells the main session to.
- Force-push. The command does not need it and neither do you.
- Run it in another team's directory or the main checkout.

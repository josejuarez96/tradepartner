# Overnight builds on free models

**Status:** Draft v0.1, a trial (#1790; [changes](#changes) at the end) · **Owner:** Jose · **Related:** [agents.md](agents.md) (model tiers, review passes), [teams.md](teams.md) (claims, team directories), [git-workflow.md](git-workflow.md#merge-classes-rule-7) (merge classes), CLAUDE.md rules 4 and 5

Free models write code overnight; frontier Claude models write the tests before and review the result after. The aim is more built tasks per week without spending plan usage on first drafts. This page is a trial: it changes no rule elsewhere, and it ends if a morning costs more review than the night saved (see [Stop rule](#stop-rule)).

## The flow

1. **Day, Claude: choose and lock.** The orchestrator picks tasks that [qualify](#what-qualifies), claims each under team `nightrun`, and has a frontier model write the task's tests on the task branch. The tests come from the plan line's `Tests:` clause, use hand values, and fail at the base for the missing feature. They are committed as `test: locked tests for <task>`; this commit is the contract.
2. **Night, free models: build.** Claude Code runs headless in `~/Projects/tradepartner-teams/nightrun`, pointed at the local FreeLLMAPI gateway, one task at a time. It edits `src/` until the locked tests, ruff and mypy pass, and commits locally. It never pushes.
3. **Night, the gate: check.** A script, not a model, checks each branch after the run. Any of these fails the branch:
   - the diff since the locked-tests commit touches `tests/` or any path outside `src/` and the task's listed extra files;
   - the locked tests, `ruff check`, `ruff format --check` or `mypy` fail;
   - the task's other test files or `tests/lookahead/` fail.
4. **Morning, Claude: review.** For each branch that passed the gate, the orchestrator runs every reviewer `ready_pr` requires for the changed paths (`quant-auditor`, and `safety-reviewer` for `config.py` or `cli.py`), plus `/code-review` at `medium`. Findings get fixed by a frontier model in one commit, as in [review passes](agents.md#review-passes). Then the normal path: fragment, push, draft PR, `ready_pr`, merge by class. A branch that failed the gate is either finished by a frontier model, if the night got close, or reset to the locked-tests commit and run again the next night.

The gate catches code that does not work. The morning review is for code that passes every test but is still wrong: look-ahead, a wrong `known_at`, a weekday instead of a session, scope creep, or code that meets the test but not the plan line.

## What qualifies

- A plan task or a `size:S` issue, claimed by `nightrun`, whose files are `src/` plus tests a frontier model can write in the day.
- **Never the order path or secrets:** no file under `src/tradepartner/execution/`, the broker adapters (`adapters/broker*`, `alpaca_raw*`, `alpaca_trading_raw*`, `fake_broker*`), `cli_record.py`, `errors.py`, `llm/` or the override page. The `SAFETY_PREFIXES` paths `config.py` and `cli.py` may be built overnight; their morning review includes `safety-reviewer`.
- No task whose tests are the work (test infrastructure such as T216), no store-writing release, and nothing on the ADR 0023 pause list.
- No overlap with an open PR's files unless the plan line allows it (rule 3's exception); the morning merges `main` first.

## What free models may never do

Enforced by the overnight session's settings, not by trust:

- Read `.env`, the gateway's directory, `~/.claude`, `~/tradepartner-probes` or the main checkout. Free providers may keep what they are sent, so they see the repo's code and nothing else.
- Edit `tests/`, `docs/`, `scripts/`, `.claude/`, `.github/`, `pyproject.toml` or `uv.lock`.
- Push, call `gh`, use the network, switch or reset branches, add dependencies, run `uv run tradepartner` or spawn subagents. Anything not allowed is denied without a prompt (`dontAsk`).
- The session runs `--bare`: no hooks, no plugins, no auto-memory, no keychain or OAuth, so it cannot fall back to the plan's Claude models.

## The setup

| Part | Where |
|---|---|
| Gateway | FreeLLMAPI, a pinned release in `~/freellmapi`, listening on `127.0.0.1:3001` only; provider keys are entered in its dashboard and stored encrypted in its own database, never in this repo |
| Gateway key | `~/freellmapi/.unified-key` (mode 0600), read by the session's `apiKeyHelper` |
| Build directory | `~/Projects/tradepartner-teams/nightrun`, team `nightrun`, its own venv |
| Launcher, brief, settings, gate | `~/tradepartner-probes/overnight/`, outside the repo; run reports in `runs/<date>/` |
| Routing | `auto:smart` (the gateway picks the strongest free model with quota left) |

The session is started per task with the gateway variables set only for that process (`ANTHROPIC_BASE_URL=http://127.0.0.1:3001`, `ANTHROPIC_MODEL=auto:smart`), so no other Claude Code session is affected. Each task has a turn cap and a wall-clock cap.

## Stop rule

After each night, the morning report records per task: gate pass or fail, the number of review findings and their severity, and the frontier time spent fixing. The trial ends, or narrows to smaller tasks, when two nights in a row cost more frontier work than writing the tasks directly would have.

## Changes

| Date | Change | Where |
|---|---|---|
| 2026-10-18 | Draft v0.1: the flow, what qualifies, the limits, the setup, the stop rule | #1790 |

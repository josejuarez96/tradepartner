# Status

**Updated:** 2026-09-24 · **Phase:** 2, Data foundation · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

## Done
- Repo scaffold: uv / ruff / mypy / pytest, CI, pre-commit, GitHub templates, labels ([initial commit](https://github.com/josejuarez96/tradepartner/commits/main))
- #1 Ways of working: git workflow, process, docs, agents, draft charter (PR #2, merged). The main session may merge a PR when the owner explicitly says to

- #5 ADRs 0003 (data adapters, local-first) and 0004 (tooling adopt/avoid) (PR #6, merged)
- #7 Charter accepted; ADRs 0005 (objective, benchmarks, stop criteria) and 0006 (universe, cadence) (PR #9)
- #8 Phase 2 spec and plan (PR #11). #12 Phase 1 retro, v0.1.0 tagged

- Phase 2 T1: config, dependencies, calendar (PR #17)
- Phase 2 T4: store schema, db layer, shared test loader (PR #20)
- Phase 2 T2: raw-fetch clients (Alpaca, EDGAR) and fixture recorder, with secret/email/User-Agent scrub (PR #21)
- #36 Multi-team orchestration: `scripts/team.py` (register, claim, release, status, check-claims), [teams.md](ways-of-working/teams.md), plan chains, model tiers, CI `claims` job (PR #37). Duplicate T5/T20 work from two unsynchronised windows cleaned up (#34 → #31, #26 → #27, both parked)
- Phase 2 T20: `Broker` interface and in-memory fake broker, no risk logic (PR #27)
- Phase 2 T5: fixture-universe generator and CSVs covering every spec req 13 case (PR #31)
- #30 Shared tz-aware UTC check `tradepartner.timeutil.ensure_tz_aware_utc` used by `store.db` and `adapters.broker` (PR #41)
- #43 `insert_row` binds the UTC-normalized `TIMESTAMPTZ` value (one canonical form); `ensure_tz_aware_utc` raises `ValueError` (not `OverflowError`) near `datetime.min`/`datetime.max`
- #53 Plan amendment: T21a dashboard shell split out of T21, depends only on T4; T21 now depends on T18 and T21a, T19 also on T21a (PR #62)
- #57 Broker-level tests: a UTC-overflowing timestamp raises `ValueError` from `Order`/`Fill` and fails closed in `FakeBroker.submit`/`simulate_fill` (PR #63). Clock-failure exception design split to #64

## Teams
New session: `uv run python scripts/team.py start <name>`, then work only in the directory it prints (`../tradepartner-teams/<name>`). (#40)
Live board: `uv run python scripts/team.py status`. Snapshot 2026-09-24: registered teams are `atlas` (main clone), `orion` (holds #22 / T5, PR #31), and `creed` (completed T20 via PR #27).

## In progress
- **Owner task T3:** run `python -m tradepartner.cli_record` with Alpaca and EDGAR keys, record source facts
- **Owner cleanup (agents are blocked from this):** remove stopped worktrees `.claude/worktrees/creed`, `.claude/worktrees/agent-aee102ec0271d2d42`, `.claude/worktrees/agent-a129ea15457cc0456`, `.claude/worktrees/agent-a45566833d1de69d7`, `.claude/worktrees/agent-ab09d798989cbab99` (locked), `.claude/worktrees/agent-ae7801cf88e3ca4a3` (team creed, done with T20); delete remote branches `feat/25-fixture-universe`, `feat/24-fake-broker`, and local branch `feat/23-fake-broker`

## Ready frontier snapshot (not a claim; only doc-keeper edits this)
Copied from `team.py status` on 2026-09-24. Claim through the tool, never from this list.
1. T5 (issue #22, held by orion, PR #31): rebase, run the required reviews, mark ready
2. Unclaimed issues: #28, #29, #30, #35 (`size:S`), #32 (type:fix), #38 (symbol-case canonicalization, type:feat). #33 is a Phase 4 idea, leave it
3. After T5 merges: T6, then T7 and T8 in parallel (see plan chains)
4. Remaining handoff §12 decisions (risk rules, execution, logging schema, LLM role) become ADRs in the phase that needs them; G1–G8 research likewise (G8 at Phase 3 start)
5. Decide whether to upgrade to GitHub Pro to enforce the `main` ruleset server-side

## Blocked
- none

## Decisions needed from owner
- none until T3
- Before Phase 6 only: account type, employer compliance check

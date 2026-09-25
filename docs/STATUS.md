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

## Teams
Live board: `uv run python scripts/team.py status`. Snapshot 2026-09-24: `atlas` (the main clone) holds #36; no other team registered. The two windows that produced parked PRs #31 (T5) and #27 (T20) were stopped during the cleanup; whichever team re-claims issue #22 / #23 continues those branches after a rebase.

## In progress
- **Owner task T3:** run `python -m tradepartner.cli_record` with Alpaca and EDGAR keys, record source facts
- **Owner cleanup (agents are blocked from this):** remove the four stopped worktrees under `.claude/worktrees/` (`git worktree remove <path>`, one is locked) and delete remote branches `feat/25-fixture-universe`, `feat/24-fake-broker`

## Ready frontier snapshot (not a claim; only doc-keeper edits this)
Copied from `team.py status` on 2026-09-24. Claim through the tool, never from this list.
1. T5 (issue #22, parked PR #31) and T20 (issue #23, parked PR #27): re-claim, rebase, run the required reviews, mark ready
2. Unclaimed fixes filed by the stopped teams: #28, #29, #30, #32, #35 (all `size:S`). #33 is a Phase 4 idea, leave it
3. After T5 merges: T6, then T7 and T8 in parallel (see plan chains)
4. Remaining handoff §12 decisions (risk rules, execution, logging schema, LLM role) become ADRs in the phase that needs them; G1–G8 research likewise (G8 at Phase 3 start)
5. Decide whether to upgrade to GitHub Pro to enforce the `main` ruleset server-side

## Blocked
- none

## Decisions needed from owner
- none until T3
- Before Phase 6 only: account type, employer compliance check

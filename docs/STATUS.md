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
- Phase 2 T20: `Broker` interface and in-memory fake broker, no risk logic (PR TBD)

## In progress
- **Owner task T3:** run `python -m tradepartner.cli_record` with Alpaca and EDGAR keys, record source facts

## Next up
1. T5 (fixture universe) and T20 (fake broker) in parallel via `implementer`
2. Remaining handoff §12 decisions (risk rules, execution, logging schema, LLM role) become ADRs in the phase that needs them; G1–G8 research likewise (G8 at Phase 3 start)
3. Decide whether to upgrade to GitHub Pro to enforce the `main` ruleset server-side

## Blocked
- none

## Decisions needed from owner
- none until T3
- Before Phase 6 only: account type, employer compliance check

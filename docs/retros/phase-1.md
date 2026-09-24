# Retro: Phase 1 (Charter & decisions)

**Date:** 2026-09-24  ·  **Tag:** v0.1.0  ·  Covers Phases 0 and 1, which both closed on the same day.

## Outcome vs exit criteria

| Criterion | Result |
|---|---|
| Ways of working merged; charter draft exists (Phase 0) | Done, PR #2 |
| Charter accepted | Done, PR #9 |
| ADRs for objective/benchmark, universe and cadence, data adapters, tooling, LLM role | 0003–0006 accepted. **LLM role: deferred**, not decided. The roadmap keeps Phase 5 conditional on a future ADR; the charter defers the layer beyond the MVP. That satisfies "even if the answer is none yet" |
| G1–G8 research done, deferred to the phase that needs it, or dropped | All deferred: G8 (vendor docs) to the Phase 3 vendor ADR; G1–G4 to the Phase 3 strategy spec; G5–G6 dropped with social data and LLM trading; G7 (wash sales) to Phase 6 |
| Paid vendor and budget deferred to Phase 3 | Done, ADR 0003 rule 8 |

Extra, not in the criteria: Phase 2 spec and plan merged (PR #11), so Phase 2 can start immediately.

**Hours:** ⬜ owner to fill in (build / review) at the next retro; this phase was one long session.

## What worked

- **Adversarial review of decisions before code.** `spec-critic` ran five times across ADRs, the charter, and the spec/plan. Each pass found something that would have produced a wrong number silently rather than a failing test: an unmeasurable survivorship metric, a circular look-ahead test, a snapshot-sourced master that made every historical universe empty, missing Form 25-NSE, back-dated corporate-action revisions, transfer detection that read the future. Cost: about three spec revisions. Worth it.
- **Deferring the vendor and budget** unblocked building without pretending free data is unbiased: the gap is measured and gates the holdout.
- **Small decisions, one PR each**, with the owner's answer recorded in the ADR text and the PR description.

## What didn't

- **Bookkeeping drift.** Twice, STATUS and ADR statuses were written as "pending" and needed a follow-up. Fixed by writing them as-if-merged in the same PR.
- **Stacked PRs.** Both times a base PR squash-merged, the stacked PR broke (one needed a manual rebase, one was auto-closed and re-opened as a new number). Stacking saved review noise but cost more than it saved.
- **One merge went in before CI reported** on a rebased commit (PR #11). CI on `main` passed afterwards, but the rule is green-before-merge.
- **The first-draft spec was too optimistic** about what free sources provide (security type, exchange, historical tickers). The verify-before-plan lists in ADRs 0003 and 0006 now carry those unknowns explicitly.

## Agents: keep / change / delete

- `spec-critic`: **keep**, the most valuable agent this phase.
- `implementer`: starting now on T1; judge at the Phase 2 retro.
- `researcher`, `quant-auditor`, `safety-reviewer`, `doc-keeper`: not yet used; judge at the Phase 2 retro.
- Candidate `data-validator`: created in plan task T23.

## Changes to ways of working (made in this PR)

1. **No stacked PRs.** Wait for the base PR to merge, then branch from `main`. ([git-workflow.md](../ways-of-working/git-workflow.md))
2. **Wait for the CI run on the exact commit** before merging; "no checks reported" is not green. ([git-workflow.md](../ways-of-working/git-workflow.md))
3. **Bookkeeping is written as-if-merged inside the PR**: STATUS "Done", ADR `Accepted`, CHANGELOG line. No follow-up PRs to record a merge. ([development-process.md](../ways-of-working/development-process.md), Definition of Done)

# Plan: <feature>

**Spec:** [specs/<feature>.md](../specs/<feature>.md)  ·  **Status:** Draft | Approved | Done

## Approach (short)
<!-- Key design choices; link ADRs. -->

## Tasks
Each task = one branch = one PR (~≤400 lines). Tasks with no shared files may run in parallel.
Shape (development-process.md, "Plan shape"): slice by file, not by step; no open chain over six PRs without a reason; no file named by more than two unticked tasks unless they are one chain; owner tasks gate only the edge, with the stub declared on the waiting line; paste `uv run python scripts/team.py graph` in the approach.

- [ ] **T1: <title>**. Files: `…` · Tests: `…` · Depends on: n/a · Review: quant-auditor?
- [ ] **T2: <title>**. Files: `…` · Tests: `…` · Depends on: T1

## Verification
<!-- How we'll demonstrate the spec's acceptance criteria end-to-end -->

## Rollback
<!-- How to undo if it goes wrong (esp. data migrations, scheduled jobs) -->

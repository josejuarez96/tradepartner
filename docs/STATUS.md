# Status

**Updated:** 2026-10-07 · **Phase:** 1 closed; 2 Data foundation (30/32) and 3 Strategy lab (22/29) in build · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

## Recently done
Phase 1 backtest closed: T45b real-store H1 run (in-sample 2020-08-31..2023-12-29, 40 monthly returns); trial 2 PASS (DSR 0.7262 psr basis, costs 1.25% at 15 bps); trial 1 FAIL on #853 disclosed; quant-auditor verdicts linked in PR. What's next: open holdout gates #1017/#1018 (gap coverage, RVTY/FI), Phase 2 data tasks, T116c evidence, strategy lab, research labeling. Pending follow-up issues: S1 (listing ends 78 Form 25 re-opens), S3 (PerkinElmer/Revvity, WTW bars).
- T98 (#1072, PR #1075): engine, holdout and run path at cadence: engine.run at the frozen rebalance_cadence, signal frame read from A_form, holdout window and gap gate at cadence; H1 at month_end byte-identical.
- #1073 trial_rebalances gains the six profitability rebalance counts (schema v13, T85d)
- ADR 0014 and the strategy-interface plan (#1074): strategies as registered objects; T85e amended into the one dispatch seam with the PAPER_FAMILIES gate; T127 to T130 (generic exclusions and counts, registry, benchmarks, B4 proof).
- T106 quiet intervals (pure) in review (#1078)
- #1084: signals.gross_profitability raises ValueError when t is not a session close (mid-session, after close, weekend, holiday); half-day closes pass.
- #1093: EdgarConfig refuses inf/NaN and caps requests_per_second at 10 (no config can drop the SEC throttle); config sections hide input values in errors; hypothesis-file errors show key = file value.
- T98b (#1094): look-ahead suites (truncation, prefix, revisions, plan-read timing) and the bt oracle parametrised over month_end, week_end and daily; month_end walk unchanged; asof tie-order follow-up #1099.
- T102 sweep file parser and grid (pure) in review (#1096)
- Rule 7: a PR that only ticks and collapses its own plan task line stays class A (#1110)
- Follow-up rule (#1123): review follow-ups are fixed in the PR (<15 min), dropped (nits) or filed as one size:S issue only for a real defect or owner decision; the 2026-10-07 triage closed 70 issues into bundles #1116-#1122.

## Teams
Active: orchestrator tradepartner-fd (atlas); profreads #1071 (T85c), codexseam #1103 (T85e), cishard #1112 (CI), kilo #1107 (config). Parked PRs: #305 (T258), #299 (T298), #297 (T294).

## In progress
B3 path running (orch tradepartner-fd): T85c #1115 → T85e #1105 → T78 #1127 (re-ingest). Draft PRs: CI #1113, config #1125/#1109. Parked: #299, #297 (owner task gates).

## Ready frontier snapshot
Claim through `team.py claim`, read task lines for gates. Ready: T22, T77c, T116c, T75b (owner); T48b, T70 (parked); T121, T125 (owner); T83–T106 (phase 3/4). Queued: #1122–#1126, #1017–#1018 (holdout gates).

## Blocked
T85e blocks T85f, T78, T23; T78 blocks T77c, phase 2 data close; holdout gates #1017/#1018 hold T45b's next run; parked #299 blocks T48c; #297 blocks T70. Triaged #1116–#1122.

## Decisions needed from owner
Open gates #1017/#1018 (holdout runs), re-run #1127 (T78), merge T85c/T85e/T85f. Then: #521 (T72b) → #528 (T74), #520, #445. Questions: #649, #821, #828, #878, #969, #987, #1011, #1016, #1034, #1060, #1085, #1116, #1119.

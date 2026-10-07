# Status

**Updated:** 2026-10-07 · **Phase:** 1 closed; 2 Data foundation (30/32) and 3 Strategy lab (22/29) in build · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

## Recently done
Phase 1 backtest closed: T45b real-store H1 run (in-sample 2020-08-31..2023-12-29, 40 monthly returns); trial 2 PASS (DSR 0.7262 psr basis, costs 1.25% at 15 bps); trial 1 FAIL on #853 disclosed; quant-auditor verdicts linked in PR. What's next: open holdout gates #1017/#1018 (gap coverage, RVTY/FI), Phase 2 data tasks, T116c evidence, strategy lab, research labeling. Pending follow-up issues: S1 (listing ends 78 Form 25 re-opens), S3 (PerkinElmer/Revvity, WTW bars).
- T98 (#1072, PR #1075): engine, holdout and run path at cadence: engine.run at the frozen rebalance_cadence, signal frame read from A_form, holdout window and gap gate at cadence; H1 at month_end byte-identical.
- #1073 trial_rebalances gains the six profitability rebalance counts (schema v13, T85d)
- ADR 0014 and the strategy-interface plan (#1074): strategies as registered objects; T85e amended into the one dispatch seam with the PAPER_FAMILIES gate; T127 to T130 (generic exclusions and counts, registry, benchmarks, B4 proof).
- #1084: signals.gross_profitability raises ValueError when t is not a session close (mid-session, after close, weekend, holiday); half-day closes pass.
- #1093: EdgarConfig refuses inf/NaN and caps requests_per_second at 10 (no config can drop the SEC throttle); config sections hide input values in errors; hypothesis-file errors show key = file value.
- T98b (#1094): look-ahead suites (truncation, prefix, revisions, plan-read timing) and the bt oracle parametrised over month_end, week_end and daily; month_end walk unchanged; asof tie-order follow-up #1099.
- T102 sweep file parser and grid (pure) in review (#1096)
- Rule 7: a PR that only ticks and collapses its own plan task line stays class A (#1110)
- Follow-up rule (#1123): review follow-ups are fixed in the PR (<15 min), dropped (nits) or filed as one size:S issue only for a real defect or owner decision; the 2026-10-07 triage closed 70 issues into bundles #1116-#1122.

## Teams
Orchestrator tradepartner-fd (session 7182c2, team atlas, main checkout); live: profreads #1115 (T85c), cishard #1113 (CI shards), kilo #1109, codexcfg #1125 (#1120). T85e #1105 is the orchestrator's (taken over from codexseam). Handovers: issue #836.

## In progress
- B3 path: T85c #1115 (ready_pr) → T85e #1105 (suites gain the profitability cases after #1115; class B) → T85f (owner). T78 #1127: statement-facts re-ingest running on the real store, then checks and the default-flip PR (class B).
- 2026-10-07 triage closed 70 issues; real defects are bundled in #1116 to #1122 (#1120 in PR #1125).

## Ready frontier snapshot
Claim through `team.py claim`; read each task line for gates. Agent: T83, T83b, T83c, T89, T90, T97, T100, T101, T122 (after T121b), bundles #1116 to #1122. Owner: T48b (#299 parked), T116c, T75b, T125. Parked: #305 (#258), #297 (T70).

## Blocked
T85f waits on T85e and T78. T70 waits on Probe 3 (#297). T48c waits on the T48b recording (#299). Holdout runs wait on gates #1017 and #1018.

## Decisions needed from owner
- Merge when ready: #1113 (CI shards), #1105 (T85e), this fold, the T78 default flip.
- Green-light #1018's fix (bare-CIK shares mapping) and #1017's last-three-months call.
- Questions on #649, #821, #828, #878, #969, #987, #1011, #1016, #1034, #1060, #1085, #1116, #1119.

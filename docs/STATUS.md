# Status

**Updated:** 2026-10-08 · **Phase:** 1 closed; 2 Data foundation (40/43) and 3 Strategy lab (28/29) in build · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

## Recently done
Phase 1 backtest closed: T45b real-store H1 run (in-sample 2020-08-31..2023-12-29, 40 monthly returns); trial 2 PASS (DSR 0.7262 psr basis, costs 1.25% at 15 bps); trial 1 FAIL on #853 disclosed; quant-auditor verdicts linked in PR. What's next: open holdout gates #1017/#1018 (gap coverage, RVTY/FI), Phase 2 data tasks, T116c evidence, strategy lab, research labeling. Pending follow-up issues: S1 (listing ends 78 Form 25 re-opens), S3 (PerkinElmer/Revvity, WTW bars).
- Engine: a negative position value now raises ValueError instead of being silently dropped (ADR 0015 TE5); zero and dust unchanged. (#1255)
- T111 (#1257): the lab CLI: sweep register|run|status|report|promote|retire and lab status; backtest <variant-slug> exits 2 refused_variant; un-migrated store exits 2 naming the lab migration.
- ADR 0015's seams are plan tasks T132 to T139 in the paper-trading plan (#1258): schema version 17, the book token, two named refusals, broker read-side types before T48c, explicit cadence, the instrument id rule, the docs task (class B)
- test_ops_page no longer flakes across a session boundary (#1263)
- #1266 T132: schema version 17, the ADR 0015 expansion seams (plan T132)
- #1267 Broker read-side types (ADR 0015 seam 3, T135)
- Every security_id is read as an instrument id, and the master's own id derivation refuses an input that would put ':' into a derived id (ADR 0015 seam 6). (#1269)
- #1271 T139: expansion direction note, deferred triggers, ADR 0010 pointers, instrument-id rule
- B4 combined family through the registry: signal, config, fixture and tests (T130) (#1275)
- paper.book_id flows from config through the window into the client order id (#1279)

## Teams
Orchestrator session bd55db6f (main checkout, team atlas); live: sideguard #1282 (T134, PR #1283), cadpath #1281 (T136, PR #1284), tidyfold #1285 (this fold). Atlas holds T22 #1264. Handovers: issue #836.

## In progress
- ADR 0015 seams: T132, T133, T135, T137, T138, T139 merged; T134 (risk.py, phases.py, wrapper.py, lots.py) and T136 (run, planning, plan, marks, outcomes, check, report, window, holdout) are in draft PRs. T135b waits on T134.
- T22 (#1264): first scheduled ingest 2026-10-08 18:30 ET; it also migrates the owner's store to schema 17. Five consecutive ok runs needed, earliest 10-12 (10-14 for five distinct sessions).

## Ready frontier snapshot
Claim through `team.py claim`; read each task line for gates (board-ready is not plan-ready). Agent: T123c (review page; T124 follows T120, T123, T123c). Owner: B4 in-sample run (registered via T130), T114, T116c, T75b, T125, T48b (#299 parked). Parked: #305 (#258), #297 (T70).

## Blocked
T135b waits on T134. T48c waits on the T48b recording (#299). T70 waits on Probe 3 (#297, #182). T23 waits on T22's evidence.

## Decisions needed from owner
- Merge when ready: this fold (class B), the T134 and T136 PRs (#1283, #1284).
- Register and run B4 in-sample: does profitability add to momentum.
- Open decisions: #969 (good-enough threshold for data fixes), #878 (Phase 6 stop criteria).
- Questions on #649, #821, #828, #1011, #1016, #1034, #1060, #1085.

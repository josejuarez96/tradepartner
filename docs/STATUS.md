# Status

**Updated:** 2026-10-12 · **Phase:** 1 closed; 2 Data foundation (70/98), 3 Strategy lab (28/29) and 4 Paper trading (81/90) in build · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

## Recently done
Phase 1 backtest closed: T45b real-store H1 run (in-sample 2020-08-31..2023-12-29, 40 monthly returns); trial 2 PASS (DSR 0.7262 psr basis, costs 1.25% at 15 bps); trial 1 FAIL on #853 disclosed; quant-auditor verdicts linked in PR. What's next: open holdout gates #1017/#1018 (gap coverage, RVTY/FI), Phase 2 data tasks, T116c evidence, strategy lab, research labeling. Pending follow-up issues: S1 (listing ends 78 Form 25 re-opens), S3 (PerkinElmer/Revvity, WTW bars).
- #1707: plan line T192b (fill-holes lists mid-month and first-day gaps, 141 E2 member-months) and the spec's time-aware contested rule (T192, #1696).
- T196: universe rule 2 passes on any live listing on a listed exchange when the newer row is a secondary quote venue (NYSE_CHICAGO, NYSETX); OTC/NONE and Form-25-ended still fail; read-time, no release (#1710)
- #1712 Spin-off writer stamps known_at at health's ex-date floor
- T189c slice 1: OpenFIGI mapping client and synthetic fixture built; schema, ingest and health follow in slices 2 and 3. (#1713)
- #1714 T190b: a pre-trading seed shares fact is no baseline (PR #1716)
- #1720 `health --check` after the 2026-10-15 ingest: a FINRA "D" OTC pair and two dividend groups
- T199 (#1722): the C2 done-test -- an AEP-shaped static NASDAQ row with a 2019 NYSE 10-K and a 2021 NASDAQ 10-K, plus the snapshot_static-evidence, writer-path look-ahead test.
- ADR 0023 break first (#1731): D1-D9 recorded; paper, backtest and data-foundation specs and plans amended (T200-T214, pause list, kill ladder B10 -> B9 -> IRRX); #878 and compliance settled in ADR 0005, roadmap, charter.
- #1734 IRRX kill-ladder rung 2: not decided from open data (no public industry-relative reversal series; needs firm-level returns), so IRRX's long leg moves to a store-side computation.
- T170b: metrics.risk_free_source/series/basis frozen keys (defaults constant/DTB3/discount, table entries at schema 28, no fingerprint move) and DataProvider.rates(t, series) over rates_as_of; T170c wires the engine. (#1744)

## Teams
Orchestrator session 2351048c (main checkout, team atlas); window tradepartner-c4. No live build teams. Atlas holds T22 #1264. Handovers: issue #836.

## In progress
- Merged 2026-10-12: #1548 #1531 #1569 #1567 #1578 #1557 #1579, and earlier the same day #1564 #1565 #1558 #1570 #1571 #1573. The store is still at schema 23; the main checkout's next store write migrates it to 24 (T171's `borrow_status` table).
- #1382 stamps repair: release `restamp-1382a3` cache step done (4,978 re-stamped, 3,317 still unrepaired on 18 CIKs with pre-2003 headers); backup and `before` record written; the store step is NOT run (owner hold). `health --check` still fails on `no_bars_after_delisting` and `underived_master_rows`; the ops release (#1549, #1545) is not started. Every logged trial is stale.
- Paper (ADR 0017): H1 window 1 (book `main`) open, next rebalance 2026-10-30; the Daily book is ok; book `main` is HALTED by the kill switch on a 2-cent cash mismatch (owner decision #1290).
- Cover-page listings (#1541): T176, T178, T179, T180 and T182 merged; the flags stay off until the owner's T181 release.
- Rates and risk-free: T169, T169b, T169c and T170 merged; T169d and T170b are next. T171 (borrow table) and T172 merged. Labelling: T174 merged; T174c next. The Sharadar annual plan is bought, the T144 overlap test is the owner's.
- T22 (#1264): scheduled-ingest evidence (five consecutive scheduled `ok` runs).

## Ready frontier snapshot
Claim through `team.py claim`; read each task line for gates (board-ready is not plan-ready). Agent: T169d (rates ingest), T170b (risk-free keys), T171b, T171c, T171e (borrow status), T174c (Jev-only `arms` key), T145 (vendor and budget ADR), T150 (after T144's verdict and T145), T164e (its real-store run waits for the restamp release). Owner: T116c, T141, T144, T75b, T71, T159, T125, T114 (box unticked; the sweep ran), T166c, T181. Unclaimed issues: see `team.py status`.

## Blocked
T165e, T23 (T22's evidence), T146-T149b (after T144's verdict and T145), T169e and T170c-T170d (the rates/risk-free chain), T171d (owner, after T171b/c/e), T172b-T172e (after T172b), T160-T163c (the Daily book and the shakedown span), T71b, T126 (#1351's seeded-recall gate, the owner's call), T181 (after T166c's release).

## Decisions needed from owner
- Merge when ready (class B): this fold.
- #1290 (the 2-cent cash mismatch that halted book `main`), #1551 item 2, #1576 (should `health --check` refuse `--no-coverage` while the coverage check is enabled), #1524 (restamp gate reads before the window's first session).
- #1382: the `restamp-1382a3` store step (owner hold) and which stale trials to replay.
- Open decisions: #1468, #1301, #411, #969, #878.

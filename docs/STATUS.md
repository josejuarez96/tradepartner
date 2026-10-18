# Status

**Updated:** 2026-10-18 · **Phase:** 1 closed; 2 Data foundation (70/98), 3 Strategy lab (28/29) and 4 Paper trading (81/90) in build · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

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
Direction: [ADR 0023](decisions/0023-break-first-fast-books-paper-exam.md) (#1740): break first, fast books, paper as the exam. The D5 pause list (T189c, #1598, T187-T187c, T184h, T174c and labeling, T171d, T23, T116c, T141, T204) is stopped; lines stay, no claims. Orchestrator: window tradepartner-82 (main checkout, team atlas). Live teams and claims: `team.py status`. Handovers: issue #836.

## In progress
- Paper: the Daily book is halted on its first rebalance (`GEF-B` asset lookup, #1729); fix #1730 is in ready_pr (with #1735 retry, ready). Book `main`: first rebalance 2026-10-30.
- Data: #969 closed by D1. One data sitting, T197 then T186c then #1382 a3 then T198, targeted 2026-10-24..27. #1728 resolver fix: #1736 (draft).
- Kill ladder: B9 killed and B3 retired (recorded in #1768, draft); IRRX parked behind T198; B10 is next, rung 1 waits on about 10 sessions of Daily fills.
- Open PRs (draft unless noted): #1730 and #1735 (ready); #1727 T190d class link (ready); #1736 rename fix; #1738 slippage line; #1750 T192b fill-holes; #1748 T212 dollar-volume rank; #1746 T207 vectorised valuation; #1773 T170c risk-free engine; #1771 mark held name missing S-1 bar; #1769 retry read-only store opens; #1768 B9/B3 verdicts; #1772 plan dependency edits; #1767 and #1777 docs passes (docsw1, docsw2); #1766 tooling; #1755 and #1756 CI shards.

## Ready frontier snapshot
Claim through `team.py claim`; read each task line for gates (board-ready is not plan-ready). Agent: T195, T213 (#1728), T200 and T203 (PRs open: #1735, #1738), T187 and T189c and T174c are on the pause list. Owner: T197, T186c, T71, T159, T75b, T114, T166c; T116c, T141, T184h are paused. Unclaimed issues: see `team.py status`.

## Blocked
T23 (paused), T204 (paused until its trigger), T146-T149b (after T144's verdict and T145), T160-T163c (the Daily book and the shakedown span), T71b, T126 (#1351 seeded-recall gate, the owner's call). #1728's repair-resolution waits on #1736.

## Decisions needed from owner
- Merge when ready (class B): this fold (#1778).
- Open: #1686 (use API stamps for SGML anchors that 404), #1676 items 3-5 (spin-off and name-change), #1524 (restamp gate before the window's first session), #411 (reconcile zero proceeds on a delisted position).
- #1382: the `restamp-1382a3` store step (owner hold), part of the 10-24..27 sitting.

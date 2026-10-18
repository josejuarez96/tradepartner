# Status

**Updated:** 2026-10-18 · **Phase:** 1 closed; 2 Data foundation (105/133), 3 Strategy lab (29/29) and 4 Paper trading (85/101) in build (counts as of 10-18) · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

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
- Paper: the Daily book resumed 2026-10-18 after the `GEF-B` fix (#1730, 5xx retries #1735, both merged); it runs Mon 08:05. Book `main`: first rebalance 2026-10-30.
- Data: #969 closed by D1. One data sitting, T197 then T186c then #1382 a3 then T198, targeted 2026-10-24..27. #1728 resolver fix: #1736 (draft).
- Kill ladder: B9 killed and B3 retired (recorded in #1768, merged); IRRX parked behind T198; B10 is next, rung 1 waits on about 10 sessions of Daily fills.
- Open PRs (draft unless noted): #1727 T190d class link, #1769 store-open retry and #1766 tooling (ready); #1736 rename fix; #1738 slippage line; #1750 T192b fill-holes; #1748 T212 dollar-volume rank; #1746 T207 vectorised valuation; #1773 T170c risk-free engine (team rfwire); #1771 and #1783 held-name marks; #1784 FakeBroker (T201); #1767 docs pass (docsw1); #1755 CI shards.

## Ready frontier snapshot
Claim through `team.py claim`; read each task line for gates (board-ready is not plan-ready). Agent: T195, T213 (#1728), T200 (merged, #1735), T203 (PR #1738), T187 and T189c and T174c are on the pause list. Owner: T197, T186c, T71, T159, T75b, T114, T166c; T116c, T141, T184h are paused. Unclaimed issues: see `team.py status`.

## Blocked
T23 (paused), T204 (paused until its trigger), T146-T149b (after T144's verdict and T145), T160-T163c (the Daily book and the shakedown span), T71b, T126 (#1351 seeded-recall gate, the owner's call). #1728's repair-resolution waits on #1736.

## Decisions needed from owner
- Merge when ready (class B): this fold (#1778).
- Open: #1686 (use API stamps for SGML anchors that 404), #1676 items 3-5 (spin-off and name-change), #1524 (restamp gate before the window's first session), #411 (reconcile zero proceeds on a delisted position).
- #1382: the `restamp-1382a3` store step (owner hold), part of the 10-24..27 sitting.

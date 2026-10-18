# Status

**Updated:** 2026-10-12 · **Phase:** 1 closed; 2 Data foundation (70/98), 3 Strategy lab (28/29) and 4 Paper trading (81/90) in build · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

## Recently done
Phase 1 backtest closed: T45b real-store H1 run (in-sample 2020-08-31..2023-12-29, 40 monthly returns); trial 2 PASS (DSR 0.7262 psr basis, costs 1.25% at 15 bps); trial 1 FAIL on #853 disclosed; quant-auditor verdicts linked in PR. What's next: open holdout gates #1017/#1018 (gap coverage, RVTY/FI), Phase 2 data tasks, T116c evidence, strategy lab, research labeling. Pending follow-up issues: S1 (listing ends 78 Form 25 re-opens), S3 (PerkinElmer/Revvity, WTW bars).
- Cover-page listings (#1541): spec and plan amendment for the exchange-axis parse fix, the title fallback, a standing coverage check and the owner's release (T178-T182); failed-banks hole disclosed in a research note (TP-4)
- #1553: `repair_stamps.py restamp-unrepaired --from <unrepaired.csv.gz>` re-stamps an earlier release's unrepaired list filing by filing from each SGML ACCEPTANCE-DATETIME (release restamp-1382a3 next, owner merges).
- #1555 follow-up: same-run statement cache refresh and separate payload-lag/no-row counters; the production cause remains open. PR #1558.
- Financial issuers: revenue_financial from RevenuesNetOfInterestExpense, else net interest income + noninterest income, per filing and period (#1556)
- T179 (#1541): classify.py title fallback behind edgar.title_fallback (default off): placeholders, whole-word types, issuer-name-first titles, class-letter shares. Not enabled until the T181 release.
- T182 built: ingest --source edgar --rebuild-classifications deletes the covered securities' classifications and rewrites the build's rows at their own known_at (PR open) (#1560)
- T180 (#1561): health's coverage check against a named reference list (coverage.* config, health --coverage, on-demand page card); report-only until T181 flips coverage.check_enabled.
- T176 merged: universe.exclude_sic_ranges defaults to none, value guard replaced by a shape check, health guarded_sic_default rule removed; stored registrations keep [[4900, 4999]]. (#1562)
- T178 (#1563): both cover-page parsers read listings tagged by dei:EntityListingsExchangeAxis behind edgar.exchange_axis_listings (default off; T181 flips it with the version bumps); mismatched MIC members are counted.
- health --check fails on a session hole: an XNYS session in the universe.min_history_months window whose prices_daily rows are below health.min_session_rows_share of the previous session's (#1575, #1550 part c)

## Teams
- Orchestrator session 2351048c (main checkout, team atlas); window tradepartner-c4. Handovers: issue #836.
- No live build teams. Atlas holds T22 #1264.

## In progress
- Merged 2026-10-12: #1548 #1531 #1569 #1567 #1578 #1557 #1579, and earlier the same day #1564 #1565 #1558 #1570 #1571 #1573. The store is still at schema 23; the main checkout's next store write migrates it to 24 (T171's `borrow_status` table).
- #1382 stamps repair: release `restamp-1382a3` cache step done (4,978 re-stamped, 3,317 still unrepaired on 18 CIKs with pre-2003 headers); backup and `before` record written; the store step is NOT run (owner hold). `health --check` still fails on `no_bars_after_delisting` and `underived_master_rows`; the ops release (#1549, #1545) is not started. Every logged trial is stale.
- Paper (ADR 0017): H1 window 1 (book `main`) open, next rebalance 2026-10-30; the Daily book is ok; book `main` is HALTED by the kill switch on a 2-cent cash mismatch (owner decision #1290).
- Cover-page listings (#1541): T176, T178, T179, T180 and T182 merged; the flags stay off until the owner's T181 release.
- Rates and risk-free: T169, T169b, T169c and T170 merged; T169d and T170b are next. T171 (borrow table) and T172 merged. Labelling: T174 merged; T174c next. The Sharadar annual plan is bought, the T144 overlap test is the owner's.
- T22 (#1264): scheduled-ingest evidence (five consecutive scheduled `ok` runs).

## Ready frontier snapshot
Claim through `team.py claim`; read each task line for gates (board-ready is not plan-ready).
- Agent: T169d (rates ingest), T170b (risk-free keys), T171b, T171c, T171e (borrow status), T174c (Jev-only `arms` key), T145 (vendor and budget ADR), T150 (after T144's verdict and T145), T164e (its real-store run waits for the restamp release).
- Owner: T116c, T141, T144, T75b, T71, T159, T125, T114 (box unticked; the sweep ran), T166c, T181.
- Unclaimed issues: see `team.py status`.

## Blocked
- T23 (T22's evidence); T165e; T71b.
- T146-T149b (after T144's verdict and T145).
- T169e, T170c-T170d (the rates/risk-free chain).
- T171d (owner, after T171b/c/e); T172b-T172e (after T172b).
- T160-T163c (the Daily book and the shakedown span).
- T126 (#1351's seeded-recall gate, the owner's call); T181 (after T166c's release).

## Decisions needed from owner
- Merge when ready (class B): this fold.
- #1290 (the 2-cent cash mismatch that halted book `main`), #1551 item 2, #1576 (should `health --check` refuse `--no-coverage` while the coverage check is enabled), #1524 (restamp gate reads before the window's first session).
- #1382: the `restamp-1382a3` store step (owner hold) and which stale trials to replay.
- Open decisions: #1468, #1301, #411, #969, #878.

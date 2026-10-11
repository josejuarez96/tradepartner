# Status

**Updated:** 2026-10-11 · **Phase:** 1 closed; 2 Data foundation (62/92), 3 Strategy lab (28/29) and 4 Paper trading (81/90) in build · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

## Recently done
Phase 1 backtest closed: T45b real-store H1 run (in-sample 2020-08-31..2023-12-29, 40 monthly returns); trial 2 PASS (DSR 0.7262 psr basis, costs 1.25% at 15 bps); trial 1 FAIL on #853 disclosed; quant-auditor verdicts linked in PR. What's next: open holdout gates #1017/#1018 (gap coverage, RVTY/FI), Phase 2 data tasks, T116c evidence, strategy lab, research labeling. Pending follow-up issues: S1 (listing ends 78 Form 25 re-opens), S3 (PerkinElmer/Revvity, WTW bars).
- T167b (#1507): `research gold --extend <n>` continues the seeded pilot permutation when the text-based count is under the floor; new cases carry draw_round, a reached seed is flipped into the draw.
- T175 done: pilot A's evidence ladder (E1/E2/cascade), E0 decisive share, hybrid router and McNemar exact paired comparisons are pure functions in research/labeling/scoring.py; reported, never gating. (#1512)
- T170 risk-free pure module (backtest/riskfree.py) and series_metrics taking a per-period risk-free sequence; risk_free_annual joins METRIC_KEYS; H1 metric rows pinned byte-identical; engine/store wiring is T170b/c. (#1513)
- T169 (#1514): schema 23 adds the empty `rates` table (`release` provenance), `RateObservation`, `rate_known_at`, the fixture rate source and a generated DTB3-shaped fixture series with the six spec cases; nothing writes or reads it yet.
- T166f in review: the in-window gate, `stamp_repair.in_window_change_allowed` and the read-only pre-check (`store/restamp_precheck.py`, `scripts/restamp_precheck.py` writing precheck.<release>.json); T166d and T166e build on it (#1497).
- T168 (#1516): the departure corpus takes every acceptance (Form 25, 8-K, marker) from the SGML header, not the late submissions JSON; corpus fetch prints its request count, stops above --max-requests; fetch_version 2.
- T166d (#1517): the #1382 stamps driver's request floor/ceiling and during-run cap, rewritten-CIK probes (3b), late API stamps (4b) and in-window earlier SGML corrections under T166f's pre-check (--window-earlier-ok).
- T166e (#1518): `ingest --restamp-from` takes `--restamp-window-earlier-ok <precheck.json>`: in-window late stamps move earlier to their SGML time under T166f's rule and a current passing pre-check; all else in the window aborts.
- Claude comparison client stays behind the research run and spend gates (#1527)
- T172: parse Alpaca spin-offs and name changes with process-date stamps and holder checks. (#1528)

## Teams
Orchestrator session fc5adf80 (main checkout, team atlas). Live: armcode #1534 (the general-purpose comparison, PR #1535), codexq #1536 (T169b rates, PR #1538), kiloq #1510 (research-labeling packets fix, PR #1531), kilor #1533 (T171 borrow table), listfu #828 (delisting follow-ups), stampguard #1382 (the stamps repair), kilos #1537 (this fold). Atlas holds T22 #1264. Handovers: issue #836.

## In progress
- ADR 0017 fast paper: H1 window 1 (book `main`) is open, next rebalance 2026-10-30; the Daily book's window 2 is open, first rebalance 2026-10-12.
- #1382 stamps repair: phase A ran 2026-10-11 as release `restamp-1382a2` — 508,539 stamp changes, 0 refused CIKs, 39 CIKs unrepaired (owner decision on #1382), no in-window corrections; the store is at schema 23. `health --check` fails on two pre-existing rules (`no_bars_after_delisting` 1,570 rows, `underived_master_rows` 2). Every logged trial is stale; the B3 trial 3 re-run comes next.
- Rates and risk-free: T169 (rates table) and T170 (risk-free module) merged; T169b-d in progress (codexq). T171 (borrow table) in progress (kilor); T172 parser merged. The Sharadar annual plan is bought and the T144 overlap test is running.
- Labelling: T167, T167b, T167c and T175 merged; the Claude API arm is not used (owner) and the comparison goes via Claude Code (#1534, PR #1535). Next: corpus re-fetch, frame build, gold.
- T22 (#1264): scheduled-ingest evidence (five consecutive scheduled `ok` runs).

## Ready frontier snapshot
Claim through `team.py claim`; read each task line for gates (board-ready is not plan-ready). Agent: T145 (vendor and budget ADR), T150 (only if buying), T164e (`filing_events` ingest and the real-store run), T169c (rates FRED client, parser, adapter and config). Owner: T116c, T141, T144, T75b, T71 (launchd `paper run` job not yet installed), T159, T125, T114 (box unticked; the sweep ran), T166c (the repair release evidence). Unclaimed issues: see `team.py status`.

## Blocked
T165e, T23 (T22's evidence), T146-T149b (only if buying, after T145), T169d, T169e and T170b-T170d (the rates/risk-free chain after T169c), T171b-T171e (after T171), T172b-T172e (after T172b), T160-T163c (the Daily book and the shakedown span), T71b, T174b, T126 (#1351's seeded-recall gate, the owner's call).

## Decisions needed from owner
- Merge when ready (class B): this fold.
- #1382: the owner decision on the 39 unrepaired CIKs from `restamp-1382a2`, and which stale trials to replay (the B3 trial 3 re-run comes next).
- Open decisions: #1468, #1301, #411, #969, #878.

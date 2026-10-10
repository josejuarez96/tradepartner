# Status

**Updated:** 2026-10-10 · **Phase:** 1 closed; 2 Data foundation (49/65), 3 Strategy lab (28/29) and 4 Paper trading (80/89) in build · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

## Recently done
Phase 1 backtest closed: T45b real-store H1 run (in-sample 2020-08-31..2023-12-29, 40 monthly returns); trial 2 PASS (DSR 0.7262 psr basis, costs 1.25% at 15 bps); trial 1 FAIL on #853 disclosed; quant-auditor verdicts linked in PR. What's next: open holdout gates #1017/#1018 (gap coverage, RVTY/FI), Phase 2 data tasks, T116c evidence, strategy lab, research labeling. Pending follow-up issues: S1 (listing ends 78 Form 25 re-opens), S3 (PerkinElmer/Revvity, WTW bars).
- T157b: `paper shakedown` (ADR 0017 E): execution/shakedown.py reads the span from the newest shakedown_span row (restarted by a mismatch or halt) and prints E.1-E.7 over every book, read-only; exit 0 only when all pass. (#1391)
- T164b (#1392, PR #1395): schema version 21 adds the empty `filing_events` table (8-K items, known_at = accepted_at), the FilingEvent record and three fixture rows; nothing writes or reads it yet (T164c-e; #1382 gates trials).
- T165c (#1393, PR #1397): B10's turnover screen in momentum (signals.turnover_screen, the strategies dispatch below 1.0; NOT_YET_APPLIED_KEYS emptied); H1's pins unchanged; screened twin in both backtest look-ahead suites. T165d next.
- T156 (#1398): paper status/report/check take --book (status --all prints one line per book); the operations page has a per-book summary and a book selector; the override page writes to the selected book's window.
- T165d done: paper-side pins for the turnover screen (H1 reads no turnover; a 0.20 book journals excluded_no_turnover; screened plan look-ahead case). (#1401)
- T155b (#1403, PR #1404): paper run drives every open book in token order, each under its own lock and paper_runs row, a crash in one never stopping the next; --book on the paper commands and paper kill --all; H1 (main) pinned unchanged.
- #1405: the recording scrub now covers every book's Alpaca paper pair (base64 Basic form), not only main's.
- T158 done: the scheduling runbook covers several books, the E.4 kill-switch drill on main, shakedown-span/note and the seven paper shakedown lines (ADR 0017; #1352).
- ADR 0018 (Proposed, #1410): the end-user app as a local FastAPI read API plus a React/TypeScript front end on one loopback port, stop/resume the only writes, phone over Tailscale, Streamlit retired at parity; the Phase 6 ADR takes 0019.
- #1414 fixed: the sweep path (lab) no longer keeps each step's marking frame, so a daily sweep's memory stays flat instead of growing ~77 MB per step; results unchanged (framefix).

## Teams
Orchestrator session e5c50a4b (main checkout, team atlas). Live (size S, disjoint files): itemkeep #1421 (T164c, PR #1428), eventasof #1419 (T164d, PR #1426), bookhalt #1400 (PR #1425), runframes #1417 (PR #1424), duckcap #1418 (PR #1423), shakebook #1416 (PR #1427), foldkeep #1429 (this fold), plus the backlog swarm: closesweep, cliwire (#607 #684), llmfence (#1312 #1085), edgarfu (#1060), execfu (#649), labtidy (#1154), specfu (#1034 #740), hypfiles (#1292 #657), evidfu (#1016 #773), succhain (#821). Atlas holds T22 #1264 and #1412 (PR #1413). Codex holds #1351 and #828. Handovers: issue #836.

## In progress
- ADR 0017 fast paper: H1 paper window 1 (book `main`) open since 10-09, first rebalance 2026-10-30. Several books, per-book keys, `paper shakedown` and the runbook are merged (T151-T158, T155b, T156). Daily book next: #1416 (sweep variant v1 as a machine-test hypothesis), then `hypothesis register`, the Daily keys and launchd job (owner), `paper start --book daily`, then the T162 shakedown span.
- T114 sweep done (run 4, trials 11-16): all six variants lose to SPY (-2.6% to -6.4%/yr excess); promotion refused; sweep `momentum-topfrac-cadence` retired on the owner's store 10-10 (decision 5). Runs 1-3 left open and empty (sleep, two out-of-memory kills fixed by #1415).
- T164 `filing_events`: T164b (schema 21) merged; T164c (adapter keeps items) and T164d (as-of read) in draft PRs; T164e follows; #1382 gates trials on it.
- Memory follow-ups to #1414: the single-run path (#1417) and a DuckDB memory_limit/threads key (#1418), both in draft PRs. #1400: the wrapper's book-mismatch refusal halts (draft PR #1425).
- T22 (#1264): scheduled ingest evidence; five consecutive ok runs needed.

## Ready frontier snapshot
Claim through `team.py claim`; read each task line for gates (board-ready is not plan-ready). Agent: T145 (vendor and budget ADR), T150 (only if buying), T164 (filing_events spec amendment and coverage count). Owner: T116c, T141, T144, T75b, T71 (launchd `paper run` job not yet installed), T159, T125, T114 (box unticked; the sweep ran). Unclaimed issues: see `team.py status`.

## Blocked
T165e, T23 (T22's evidence), T146-T149b and T150 (only if buying, after T145), T164e (T164c, T164d), T71b, T160-T163c (the Daily book and the shakedown span), T126 (#1351's seeded-recall gate, the owner's call). T48c waits on the T48b recording (#299). T70 waits on Probe 3 (#297, #182).

## Decisions needed from owner
- Merge when ready (class B): this fold, #1413 (ADR 0018 amendment; whether to run spec-critic and safety-reviewer on it), the shakebook PR for #1416.
- #1382: re-stamp the universe's ~1,000 CIKs from SGML headers under a named release, plus a guard on new stamps (recommended; the stampcheck replay left trials 2-4 essentially unchanged).
- Daily book: put the Daily paper keys in `.env` (owner only), install the `paper run` launchd job.
- Open decisions: #1408 (carried from #1011 and #987), #1351 item 1, #1301, #1290, #1293, #1237, #411, #969, #878.
- Parked by the owner: a pre-shakedown failure-drill week; raise again before T162.

# Status

**Updated:** 2026-10-02 · **Phase:** 2, Data foundation (30/32) and 3, Backtest (23/24), with 4, Paper trading (43/55) in build · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

## Recently done
The last 10, newest last; older ones are in [CHANGELOG.md](../CHANGELOG.md) and git history, pending ones in `uv run python scripts/fragments.py show` (#351).
- #542 owner decision: `window.abandon` refuses `open_orders` (nothing written, nothing cancelled) while any order of the window is non-terminal in the journal, stop's not_ready read; spec req 14 amended
- Fixed: alerts.channels=[store, email] with ALERT_* unset now refuses at config load, naming the missing variable (#544).
- Phase 4 T63d: the tracking run trades: submit window gating step 6 and the forced exits, exits journaled and handed to the wrapper's execute, step 7b collection with executed and the unspent_cash alert (#545)
- #553: the unspent_cash alert also fires when a later run's step 3 writes executed (a late fill), on the broker's cash and equity read at that moment.
- Planning now treats a Form 25 transfer the same as the exits: still listed, not an end (#555).
- Fixed engine._ended to hold a Form 25 transfer through, matching planning's #555 fix
- #564 (PR #565): build_classifications makes one ordered pass per company (5k-filing issuer 7.4 s to 0.01 s, output identical to the old code); _ingest_filings skips its redundant fetch-pass build after a prefetch.
- #566 (kite): an empty `{}` or cik-less member in companyfacts.zip or submissions.zip is treated as absent and asked of the per-CIK API, counted on facts_bulk_empty / submissions_bulk_empty; unblocks the backfill rerun.
- #572 research: EDGAR data-quality pitfalls from open-source libraries (16 pitfalls mapped to our code; gaps are per-CIK/per-row quarantine and transport retries)
- #576: a per-CIK companyfacts API 200 {} is no facts, like a 404 (facts_api_empty); a submissions API 200 {} lists nothing and leaves rows unstamped, never cached unstampable (submissions_api_empty)

## Teams
New session: `uv run python scripts/team.py start <name>`, then work only in the directory it prints (`../tradepartner-teams/<name>`). (#40)
Live board: `uv run python scripts/team.py status`. Snapshot 2026-10-02: orchestrator **tradepartner-9c** (atlas, main checkout). tradepartner-9d retiring: holds #577 (#573, team kite). tradepartner-61 holds T45b (#503). Claimed: atlas #534 (PR #574), atlas #583, ibis #535, kite #573 (PR #577), pelican #409/#410, plover #554, tamarind #510 (PR #521), tern #527 (PR #528), wren #517 (PR #520). Parked: #305, #299, #297. Dead claims for `release --force`: `meridian` #258, `eclipse` #182.

## In progress
- **Merged 2026-10-02:** #519 T63, #541 T60e, #547 T63d, #493 T63e, #506 T63g, #525 T65b, #534 frozen costs (#574 ready_pr running), #565 #567 #579 (EDGAR blockers), #539 #537 #548 #546 #543 #550 #556 #558 #559 #561 #562 #570 #575 (fixes).
- **Ready, owner's merge:** #521 T72b, #528 T74, #520 (spec), then #445 last.
- **Phase 3:** T45b (owner): backfill rerun after #577 merges; then `health --check`, `hypothesis register`, `backtest h1-momentum-12-1`.
- **Phase 4:** #574 ready_pr running (atlas #534). Ready frontier: T60e, T63d, T64b, T69, T70 ready to claim; #577 merge unblocks T45b phase-3 backfill.
- **Process:** merge-train (#521/#528) ready; #445 last. T72/T73 merged. Merge rules: #519 before #547, #519 before #539, #537 before #575 per history.

## Ready frontier snapshot (not a claim; only doc-keeper edits this)
From `team.py status` on 2026-10-02. Claim through the tool, never from this list. Ready: T22, T45b, T48b (owner), T60e, T63d, T64b, T69, T70. Parked: #445 (last, after order-path), #305, #299, #297. Unclaimed size-S: #582, #581, #580, #578, #571, #569, #568, #563, #560, #552, #551, #529, #524, #518, #507, #505, #491, #470, #435, #412, #411, #382, #376, #366, #258, #183, #182, #33.

## Blocked
Plan tasks by depth: T23, T48c, T63f, T63h, T63i, T66, T67, T69b, T71, T71b, T72c, T75, T75b waiting on their dependencies (team.py). T48c, T67, T71 wait on owner T48b; T70 on Probe 3 #182; T66 on T64b (merging); T63i, T63f, T63h still depend on unmergeable tasks. T23 on T22 evidence. T71/T71b on T60e, T63g, T63h merged; T60e merged 2026-10-02, T63g earlier; T63h suite not started. T72c–T75b depend on earlier merge-train completion (#521, #528, #445).

## Decisions needed from owner
- **2026-10-02 decisions:** #534 (frozen costs + costs_drift, spec req 14 sentence approved); #542 (abandon refuses while own orders open, #556 merged); Form 25 transfer is not a listing end (#561 merged); EDGAR quarantine stays fail-closed; #578 pre-flight validation after #577 (kite, in progress); #554 retries after #576 (done #575); #526 (freeze execution.fill_price before T71).
- **Merges:** #521 (T72b) → #528 (T74) → #445 last; #520 (spec Q19–Q24).
- **T45b (backfill rerun after #577 merges):** `ingest --backfill --since 2016-01-01`, then `health --check`, `hypothesis register`, `backtest h1-momentum-12-1`.
- **T48b (owner):** paper responses and broker facts; `cli_record paper <SYMBOL>`, flat account. Probe 3 (#182) for T70 timing keys (submit 09:00–09:15 ET, collect after 09:46).
- **Open questions:** #416, #470, #411/#412, #473 (spin-offs); #281 strategy-lab 2,8,11; event-data spec (#307).
- **Housekeeping:** `release --force` #258/#182; `team.py prune --yes`; close #33; `gap_signoff` on H1 before T71; GitHub Pro ruleset and compliance (Phase 6).

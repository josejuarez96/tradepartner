# Status

**Updated:** 2026-10-02 · **Phase:** 2, Data foundation (30/32) and 3, Backtest (23/24), with 4, Paper trading (44/55) in build · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

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
Live board: `uv run python scripts/team.py status`. Snapshot 2026-10-02: orchestrator **tradepartner-9c** (atlas, main checkout); it runs background teams in retired directories (plover #554, atlas-6a this fold #583). tradepartner-9d is retiring and holds only #577 (#573, team kite). tradepartner-61 holds T45b (#503). Other claims: ibis #535, pelican #409/#410, tamarind #510, tern #527, wren #517. Dead claims for the owner's `release --force`: `meridian` #258, `eclipse` #182.

## In progress
- **Merged 2026-10-02:** #519 (T63), #541 (T60e), #547 (T63d), #550 (#472, schema v8), #570 (#542), the EDGAR backfill blockers #565, #567, #579, and #537, #539, #543, #546, #548, #556, #558, #559, #561, #562, #575.
- **PRs:** #574 (#534 frozen costs; owner approved the `costs_drift` refusal and the spec req 14 sentence) in ready_pr; #577 (#573) in ready_pr; #554 being built (plover).
- **Owner's merges:** #521 (T72b) → #528 (T74), #520 (spec; unblocks #505 and #516), then #445 last.
- **Phase 3, T45b (owner):** all known EDGAR blockers are merged; the backfill rerun (`ingest --backfill --since 2016-01-01`) follows #577, then `health --check`, `hypothesis register`, `backtest h1-momentum-12-1`.

## Ready frontier snapshot (not a claim; only doc-keeper edits this)
Worked out from the plan after this fold's ticks (T60e, T63d, T64b, T69); `team.py status` shows it once the fold merges. Claim through the tool, never from this list; read each task line for "waits for" gates.
1. ready plan tasks: T63f, T63i, T66, T69b; T70 (#297 parked, gated on Probe 3); T22, T45b, T48b (owner).
2. parked PRs: #305 (#258), #299 (T48b), #297 (T70).
3. queued issues: #578 (after #577), #526 (before T71), #507/#560/#563 (run.py, one team), #551/#552, #435, #518 (after #534).

## Blocked
- T63h waits on T63f and T66; T67 on T48c, T63f and T66; T71 on T45b, T48c, T63h, T63i, T67, T69b and T70; T71b on T71; T23 on T22's evidence; T48c on the owner's T48b recording.
- Merge train: T72c on T72b (#521), T75 on T72c, T75b on T74 (#528) and T75.

## Decisions needed from owner
- **Merges:** #521 → #528, #520, then #445 last.
- **Open on #554:** whether a filing header that still fails after every retry counts against the failed-filings allowance (today it fails the run).
- After the backfill: `health --check`, T45b, then the ingest plist (runbook) for T22's evidence.
- A market-hours session for Probe 3 (#182) and the T48b recorder (`cli_record paper <SYMBOL>`, flat paper account).
- Open questions: #470, #411/#412, #473; #281 strategy-lab questions 2, 8 and 11; event-data spec (#307).
- `release --force` for #258 and #182; `team.py prune --yes`; close #33; `gap_signoff` on H1 before T71.
- Account type and compliance check (Phase 6).

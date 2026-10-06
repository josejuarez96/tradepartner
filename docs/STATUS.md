# Status

**Updated:** 2026-10-06 · **Phase:** 1, Backtest (closed pending merge); 2, Data foundation (30/32) and 4, Paper trading (44/55) in build · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

## Recently done
Phase 1 backtest closed: T45b real-store H1 run (in-sample 2020-08-31..2023-12-29, 40 monthly returns); trial 2 PASS (DSR 0.7262 psr basis, costs 1.25% at 15 bps); trial 1 FAIL on #853 disclosed; quant-auditor verdicts linked in PR. What's next: open holdout gates #1017/#1018 (gap coverage, RVTY/FI), Phase 2 data tasks, T116c evidence, strategy lab, research labeling. Pending follow-up issues: S1 (listing ends 78 Form 25 re-opens), S3 (PerkinElmer/Revvity, WTW bars).

## Teams
New session: `uv run python scripts/team.py start <name>`, then work only in the directory it prints (`../tradepartner-teams/<name>`). (#40)
Live board: `uv run python scripts/team.py status`. Snapshot 2026-10-02: orchestrator **tradepartner-9c** (atlas, main checkout); it runs background teams in retired directories (plover #554, atlas-6a this fold #583). tradepartner-9d is retiring and holds only #577 (#573, team kite). tradepartner-61 holds T45b (#503). Other claims: ibis #535, pelican #409/#410, tamarind #510, tern #527, wren #517. Dead claims for the owner's `release --force`: `meridian` #258, `eclipse` #182.

## In progress
- **Merged 2026-10-06:** Phase 1 T45b real-store run (trial 2 PASS; trial 1 FAIL on #853 disclosed).
- **PRs pending owner merge:** #503 (T45b phase-close, trial evidence + STATUS/CHANGELOG fold + plan tick).
- **PRs:** #574 (#534 frozen costs) in ready_pr; #577 (#573) in ready_pr; #554 being built (plover).
- **Owner's merges:** #521 (T72b) → #528 (T74), #520 (spec), then #445 last; after #503: Phase 2 data tasks resume.

## Ready frontier snapshot (not a claim; only doc-keeper edits this)
Worked out from the plan after this fold's ticks (T60e, T63d, T64b, T69); `team.py status` shows it once the fold merges. Claim through the tool, never from this list; read each task line for "waits for" gates.
1. ready plan tasks: T63f, T63i, T66, T69b; T70 (#297 parked, gated on Probe 3); T22, T45b, T48b (owner).
2. parked PRs: #305 (#258), #299 (T48b), #297 (T70).
3. queued issues: #578 (after #577), #526 (before T71), #507/#560/#563 (run.py, one team), #551/#552, #435, #518 (after #534).

## Blocked
- T71 waits on #503 merge, then T63h, T63f, T66, T48c, T63i, T67, T69b, T70; T71b on T71; T23 on T22's evidence; T48c on T48b recording.
- Merge train: T72c on #521, T75 on T72c, T75b on #528 and T75.

## Decisions needed from owner
- **Merge #503 (T45b phase-close):** trial evidence, quant-auditor PASS on trial 2.
- **Then:** #521 → #528, #520, #445.
- **After merge:** open holdout gates #1017/#1018, phase 2 data tasks resume.
- Other: #554 filing-header policy; Probe 3 session for #182; #470, #411/#412, #473; #281 strategy-lab Qs; `release --force` #258/#182; `gap_signoff` H1.
- Phase 6: activate `main` ruleset, run first train (T75b), account/compliance checks.

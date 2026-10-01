# Status

**Updated:** 2026-10-01 · **Phase:** 2, Data foundation (30/32) and 3, Backtest (23/24), with 4, Paper trading (20/50) in build · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

## Recently done
The last 10, newest last; older ones are in [CHANGELOG.md](../CHANGELOG.md) and git history, pending ones in `uv run python scripts/fragments.py show` (#351).
- #450 A sideless override (every keep_name, and an exclude_name of a name not held) is closed with its own kind as the state reason; spec closed-state list and full-exit note name it
- #453 main green: `switch.release` raises a typed `ReleaseRefused` (a `ValueError`) on every refusal; `paper resume` maps it to a refused outcome with the switch still engaged
- #455 FSN members with a non-UTF-8 byte (a 2015 SandRidge txt.tsv note) are read with the byte replaced by U+FFFD instead of failing the whole EDGAR backfill
- #456 ready_pr runs targeted local pytest (the test files a diff maps to, plus the docs-budget test always), the full suite when the mapping is unclear or with `--full-tests`; CI still runs the full suite
- #458 CLAUDE.md pre-push rule: ready_pr's targeted pytest locally (--full-tests for all), full suite in CI (companion to #456)
- #460 Merge-train spec and plan: batch ready PRs on one train branch, one full CI run, bisect on red, squash-merge only green trees on the owner's word per batch; six tasks T72 to T75b
- #462 main green again: the T60 halt-read skew test pins the halt path's utc_now() instead of racing the wall clock past the fixture's CLOCK_START
- #469 #366 batch 2 answers recorded: ADR 0010 amendment 2026-10-01 (own open buys reserved against cash) and new task T54d; spin-off rules in spec req 6 (#473 to store them); decisions_from choices confirmed on T63c
- Phase 4 T54d: `execution.reserve.open_buy_reserve`, the unfilled notional of our own non-terminal buys from any session (quantity buys split-adjusted, at the buffered price), `Decimal`, rounded up to the cent (#481)
- #489 process: windows message the orchestrator only with ready, blocked or a cross-team finding; Sonnet windows for size-S, docs-only and pure-module tasks; two review passes per reviewer per PR, the second scoped to the fixes

## Teams
New session: `uv run python scripts/team.py start <name>`, then work only in the directory it prints (`../tradepartner-teams/<name>`). (#40)
Live board: `uv run python scripts/team.py status` (2026-10-01). Active teams: atlas (main), bramble (#498), finch T60d, heron T63c, lantern #405, marigold #447, plover T65, sparrow T69, tern T64b, wren T63e. Dead claims pending `release --force`: `meridian` #258, `eclipse` #182. Retired directories wait for `team.py prune --yes`.

## In progress
- Draft PRs in flight (team.py status): T60d #494 (finch, re-attempt scope), T63c #431 ready (heron, planning), T69 #496 (sparrow, operations), T64b #495 (tern, window stop), T63e #493 (wren, exits). Ready PRs parked on owner merge: #480 (T60 fix), #479 T72 (#475), #478 T73 (#476), #445 (prefixes), #419 (alerts), #305, #299, #297.
- Ingest issues: #498 (bramble, FSN tabs). Tests: #448 ready (lantern, #405), #406-boundary-fences draft (#406-#407-#418).
- **Phase 2:** T22's tick waits on the owner's ingest runs (runbook merged, #316), then T23.
- **Phase 3:** T45b owner, backfill rerun pending, then health check, hypothesis register, backtest h1-momentum-12-1.
- **Phase 4:** frontier open: T60c, T63g pure suites; T60 fix #480 parked ready for merge; T53b waits on #350.
- **Process:** #489 process improvements merged; fold PR #499 in progress.

## Ready frontier snapshot (not a claim; only doc-keeper edits this)
Copied from `team.py status` on 2026-10-01. Claim through the tool, never from this list.
1. plan tasks ready: T22 (agent, runbook merged), T45b/T48b (owner-gated), T60c/T63g (agent pure suite), T70/T72/T73 (parked ready PRs: #297, #479, #478).
2. parked ready PRs: #480 (T60 fix), #479 (T72), #478 (T73), #445 (prefixes), #419 (alerts SMTP), #305 (ingest), #299 (T48b), #297 (T70).
3. unclaimed issues by priority: #488 M (reconcile explanations), #497/#491/#471 S (reattempts, processes, fences), then #470/#473/#472/#435 (feature/data/test issues), then size-S fixes (#416, #412, #411, #410, #409, #407, #406, #404, #403, #395, #394, #382, #381, #376, #258, #183, #182, #33, #366).

## Blocked
- Plan tasks blocked on dependencies (team.py status): T23, T48c, T60b, T60d, T60e, T63, T63d, T63f, T63h, T63i, T65b, T66, T67, T69b, T71, T71b, T72b, T72c, T74, T75, T75b.
- T60d/T65/T69 in flight; T48c/T60b/T60e/T63/T67/T71 wait on T48b (owner recording); T23 waits on T22 (owner evidence); T63i/T66 blocked on open dependencies.

## Decisions needed from owner
- Rerun backfill (#45b), then `health --check`, hypothesis register, backtest h1, then T22 plist install (evidence).
- T48b recorder and Probe 3 (#182): market hours, submit 09:00–09:15 ET, collect after 09:46; `cli_record paper <SYMBOL>` flat account.
- #488 open size question: is the reconcile_run explanations task (plan line or new task?). Judgment calls in #493 (T63e), #495 (T64b) PR bodies.
- Merged PR answers: #339 T54 ADR 0010 (per-order exit limit, spin-off rules in #473 spec change), #349 T62 (alert kind), #331 T59 (release).
- #281 strategy-lab qs 2, 8, 11; Phase 3 spec amendments; cadence ADR 0012; event-data spec (#307); event-study engine after T45b.
- `release --force` for dead claims (#258, #182); `team.py prune --yes`; close #33; gap_signoff before Phase 4 paper start.
- GitHub Pro server-side ruleset decision; account type and compliance check (Phase 6).

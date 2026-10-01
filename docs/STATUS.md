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
Live board: `uv run python scripts/team.py status`. Snapshot 2026-10-01: orchestrator **tradepartner-e3** (atlas, main checkout, where the owner runs the backfill; no git ops there meanwhile). The owner retired the day's windows; e3 runs background teams in retired directories: plover T65, finch T60d, wren T63e, tern T64b, sparrow T69, heron (#431 refresh), marigold (#466 refresh), osprey (#488 then T63g). bramble holds #498. Dead claims for the owner's `release --force`: `meridian` #258, `eclipse` #182. Retired directories wait for `team.py prune --yes`.

## In progress
- **Phase 4 PRs:** #434 T65, #494 T60d, #493 T63e, #495 T64b, #496 T69; #431 T63c and #466 (#447) being brought up to date with main; #488 (journal reads cut at `as_of`, owner chose option 1; the decision is the plan), then T63g (#486) stacked on it.
- **Ready, owner's merge:** #478 T73, #479 T72 (merge train), #419 (#394; spec adds optional `ALERT_EMAIL_FROM`), #480 (#395; spec), #501 (this fold). #445 (#381) merges last, after the order-path PRs.
- **Phase 2:** T22's tick waits on the owner's ingest runs (runbook merged, #316), then T23.
- **Phase 3:** T45b (owner): the backfill rerun (`ingest --backfill --since 2016-01-01`) after #461, then `health --check`, `hypothesis register`, `backtest h1-momentum-12-1`. #498 (embedded tabs in an FSN value) may fail EDGAR again.
- **Process:** #490 merged (report-only messages, Sonnet windows for simple work, two review passes); merge-train T72/T73 ready, T72b onward follow.

## Ready frontier snapshot (not a claim; only doc-keeper edits this)
Copied from `team.py status` on 2026-10-01. Claim through the tool, never from this list.
1. ready plan tasks: T60c once T60d, T63e and #431 merge (shared files); T63 once #431 merges; T70 (#297 parked, gated on Probe 3); T22, T45b, T48b (owner).
2. parked PRs: #467 (#406/#407/#418 fences, safety PASS, ready_pr not run), #305 (#258), #299 (T48b), #297 (T70).
3. unclaimed issues: #497, #491, #471, #470, #472, #473, #435, #416, #410, #409, #404, #403, #382, #376, #183, #33.

## Blocked
- Every other plan task waits on its dependencies (board: T23, T48c, T60b, T60e, T63d to T63i, T65b, T66, T67, T69b, T71, T71b, T72b to T75b).
- T48c, T60e, T67 and T71 wait on the owner's T48b recording; T70 on Probe 3; T23 on T22's evidence.

## Decisions needed from owner
- Merges above; the judgment calls listed in the PR bodies of #493 (T63e: spin-off receipt spent, whole-share dust loop) and #495 (T64b: abandoned `residues_json` shape, override field refusal, mismatch at the closing stop engages the switch).
- After the backfill: `health --check`, T45b, then the ingest plist (runbook) for T22's evidence.
- A market-hours session for Probe 3 (#182, submit 09:00 to 09:15 ET, collect after 09:46) and the T48b recorder (`cli_record paper <SYMBOL>`, flat paper account).
- Open questions: #416, #470 (a), #411/#412, #366 Q19 to Q24, #473; #281 strategy-lab questions 2, 8 and 11; event-data spec (#307).
- `release --force` for #258 and #182; `team.py prune --yes`; close #33; `gap_signoff` on H1 before Phase 4 paper start.
- GitHub Pro server-side ruleset decision; account type and compliance check (Phase 6).

# Status

**Updated:** 2026-09-30 · **Phase:** 2, Data foundation (30/32) and 3, Backtest (23/24), with 4, Paper trading (20/50) in build · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

## Recently done
The last 10, newest last; older ones are in [CHANGELOG.md](../CHANGELOG.md) and git history, pending ones in `uv run python scripts/fragments.py show` (#351).
- Phase 4 T52: `execution/plan.py`: `remainder` (quantity sell from the latest order split-adjusted over (order session, S], notional sell converted at the reference price, buy target minus filled value over every order, the plan… (#326)
- Phase 4 T52b: `execution.plan.residue` (carried residues split-adjusted through S, `origin = untradable` counted only while the latest `positions_daily.tradable` flag is false, `window_stop` dust and forced-exit `untradable`… (#337)
- #334 Secrets: `ingest._clean` redacts every `SecretStr` field of `Settings` (found by type, so a new secret is covered without a list edit); the paper recorder's stderr error goes through `scrub_text`; `_configured_secrets` adds… (#334)
- Phase 4 T58 (#335): `execution/collect.py`: the collector (account-wide `fills(since)` with the overlap and the reach-back to the oldest open order, insert-or-ignore, the supersede pointer, the broker-clock skew `ClockError`… (#335)
- Phase 4 T54: `execution.risk.check_phase` decides the phase-time skips (`skip_delisted`, `skip_untradable`/`untradable`, `skip_below_one_share`, `skip_below_minimum`, `dust`), the skip cap, and every ADR 0010 batch limit (target… (#336)
- Phase 4 T62: `execution/outcomes.py`, `due_outcomes` (pure) and `write_outcomes_and_lots`: one outcome per earned kind, due at the first run after close(T_{i+1}) (a forced exit after its session; a stopped window at the earliest… (#344)
- #347 main green again: removed the unused `type: ignore` (and its stale comment) in `execution/switch.py`, left after #315 made `JournalRow`'s timestamps read-only
- #351 STATUS cut to a board with the last 10 done lines; one fragment per PR; fold trims; CI token budgets on STATUS (2k) and CLAUDE.md (3k)
- #352 `team.py claim` prints the task's plan line and its dependencies' lines (`team.py show <Tn>` reprints them); implementers read those plus the cited spec sections, never the whole plan or spec; `spec-critic`, `quant-auditor`… (#352)
- #358 EDGAR `form.idx` rows with a blank company name (a 1997 SC 13D in the owner's first real backfill) parse with an empty name instead of failing the whole EDGAR source

## Teams
New session: `uv run python scripts/team.py start <name>`, then work only in the directory it prints (`../tradepartner-teams/<name>`). (#40)
Live board: `uv run python scripts/team.py status`. Snapshot 2026-09-30: **every team retired** on the owner's instruction; a new orchestrator and new windows start fresh. `atlas` (main checkout) holds nothing. Claims still on the board, for the owner's `release --force`: `meridian` #258 (Codex window, draft PR #305 with a failing CI run), `eclipse` #182 (owner-run Probe 3).
Retired directories (otegra, placid, bitfly, chawal, dante, eonic, cupertiene, ecuator, meridian and the earlier research ones) wait for the owner's `team.py prune --yes`.

## In progress
- Nothing is being built. Parked, green, for re-claim: #350 (T53, #343: resolve the plan-file conflict, rerun ready_pr; it splits T53b inside), #299 (T48b, owner-gated on the recorder run), #297 (T70, gated on Probe 3).
- **Phase 2:** T22's tick waits on the owner's five unattended ingest runs (runbook merged, #316), then T23.
- **Phase 3:** only T45b (owner): the real store does not exist yet; the first backfill (2026-09-28) failed on an EDGAR index row and the fix merged as #359; the rerun is pending (`ingest --backfill --since 2016-01-01`), then `health --check`, `hypothesis register`, `backtest h1-momentum-12-1`.
- **Phase 4:** frontier T60 (wrapper core) and T61 (reconciliation with store explanations); T53b after #350; T48c and the buys phase wait on T48b.
- **Process:** #352 merged (claim prints the task and dependency lines; reviewers post the report on the PR, return ten lines); #351 merged as #355 (this board, one fragment per PR, budget test); #353 (plan collapse, teams card) is next.

## Ready frontier snapshot (not a claim; only doc-keeper edits this)
Copied from `team.py status` on 2026-09-30. Claim through the tool, never from this list.
1. ready plan tasks: T60, T61 (agent); T45b, T48b (owner); T22 shows ready but its tick is the owner's evidence.
2. unclaimed issues, do first: #357 (ready_pr's safety path list names `exec/` not `execution/`, so order-path PRs got no automatic safety review); then #353, #356, #360 (a config test reads the project `.env`; run ready_pr with `--no-tests` from the main checkout until fixed), #332 (belongs to T60/T61b), #342, #183, #33 (closable).
3. parked PRs: #350, #299, #297.

## Blocked
- Every other plan task waits on its dependencies (board: T23, T48c, T60b to T71b).
- T48c, T60d, T67, T70b and T71 wait on the owner's T48b recording; T70 on Probe 3; T23 on T22's evidence.

## Decisions needed from owner
- Rerun the backfill, then T45b (the Phase 3 close), then install the ingest plist (runbook) for T22's evidence.
- A market-hours session for Probe 3 (#182, submit 09:00 to 09:15 ET, collect after 09:46) and the T48b recorder (`cli_record paper <SYMBOL>`, flat paper account, non-fractionable symbol); #183 likewise.
- Answers in merged PR bodies: #317 T56 (replacement shares; corporate actions, likely a req 13 amendment), #329 T52 (split basis; one-share sell), #338 T52b (zero-decision plan), #339 T54 (four ADR 0010 items; the per-order limit on exits matters most), #349 T62 (lot-ledger alert kind), #331 T59 (release stricter than spec req 5).
- #281 strategy-lab questions 2, 8 and 11; then the Phase 3 spec amendments and cadence ADR 0012.
- Event-data spec (#307): news source (question 1) and the Federal Register stamp brief (question 2); the event-study engine spec after T45b.
- `release --force` for #258 and #182; `team.py prune --yes`; close #33.
- Sign-off on H1's recorded survivorship gap (`gap_signoff`) before Phase 4 paper start, per ADR 0009 and ADR 0003 rule 8.
- GitHub Pro decision for the server-side main ruleset still open.
- Before Phase 6 only: account type, employer compliance check

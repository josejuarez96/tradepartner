# Status

**Updated:** 2026-09-27 · **Phase:** 2, Data foundation (28/32) and 3, Backtest (21/24), with 4, Paper trading (7/50) in build · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

## Recently done
The last 10, newest last; older ones are in [CHANGELOG.md](../CHANGELOG.md) and git history, pending ones in `uv run python scripts/fragments.py show` (#351).
- Phase 3 T42: `tradepartner backtest` (exit 0 ok, 1 failed, 2 refused; trial id, base-cost metrics table, strategy per cost level, gap maxima, DSR and flags; no `--synthetic`, no store-path option; `--spend-holdout` refused… (#309)
- Phase 4 T57: `execution.alerts.Alerter` writes the `alerts` row before any delivery, dedupes on (kind, run) or, for `locked`/`no_window`, (kind, session), delivers to `store`, `macos` (`osascript`) and `email` (SMTP over… (#310)
- Phase 4 T51: `execution/ledger.py`, `from_journal(fills, orders, adjustments, actions_as_of, last_ok_reconciliation, starting_cash, through, *, window_id, quantity_tolerance) -> Ledger`, pure and per window (rows after `through`… (#311)
- Phase 2 T22 (#312): scheduling runbook `docs/runbooks/scheduling.md`, with the `com.tradepartner.ingest` launchd plist, PATH for `uv`, working dir and `.env`, sleep vs power-off with a `pmset` wake, logs, TCC, the evidence query… (#312)
- #320 `cli_record._configured_secrets` includes the Phase 4 alert secrets (`ALERT_SMTP_USER`, `ALERT_SMTP_PASSWORD`, `ALERT_EMAIL_TO`), so the fixture recorder and the CLI error scrub replace them; blank values stay out
- Phase 4 T55: `execution/reconcile.py`, `compare(ledger, positions, open_orders, lagging, account, explanations, window, frozen, *, order_id_prefix) -> Reconciliation`, pure: account id, per-symbol quantity and cash within the… (#321)
- Phase 4 T59 (#322): `execution/lock.py` (the exclusive `flock` run lock on `<store.path>.paper.lock`, `LockHeld` at once, `is_held`) and `execution/switch.py` (the derived kill-switch state per window, `engage` with a… (#322)
- Phase 3 T45: `backtest-runner` agent (one registered hypothesis on a temp copy of the fixture store, `store_path` always set, synthetic trials only, no holdout or override flag, never the main checkout) added to the agents.md… (#323)
- Phase 4 T52: `execution/plan.py`: `remainder` (quantity sell from the latest order split-adjusted over (order session, S], notional sell converted at the reference price, buy target minus filled value over every order, the plan… (#326)
- Phase 4 T52b: `execution.plan.residue` (carried residues split-adjusted through S, `origin = untradable` counted only while the latest `positions_daily.tradable` flag is false, `window_stop` dust and forced-exit `untradable`… (#337)

## Teams
New session: `uv run python scripts/team.py start <name>`, then work only in the directory it prints (`../tradepartner-teams/<name>`). (#40)
Live board: `uv run python scripts/team.py status`. Snapshot 2026-09-27: `atlas` #293 (this fold), `chawal` #263 T11h (took over bitfly's parked draft PR #275, checks green), `cupertiene` #288 T49c (PR #291 draft), `dante` #287 T46c (PR #292 draft), `eonic` #289 T50 (PR #290 draft), `meridian` #294 T70, `eclipse` #182 (owner-run Probe 3, waits for Monday).
Retired 2026-09-27: otegra (T46b, #280), placid (T49b, #286), bitfly (T11h handed to chawal); their directories wait for the owner's `team.py prune`.

## In progress
- **Phase 2 critical path:** T11h (chawal, PR #275) → T19 → T22 → T23.
- **Phase 3 critical path:** T42 needs T19; T45 needs T42; T45b (owner) needs T45.
- **Phase 4:** T46c (PR #292), T49c (PR #291), T50 (PR #290), T70 (meridian, after Probe 3); T48b (owner) gates T48c, T60d, T67, T70b and T71.
- **Owner cleanup (agents are blocked from this):** prune the retired team directories; `diomedes` kept for inspection, with uncommitted changes; the closed research worktrees and two entries under `.claude/worktrees/`.

## Ready frontier snapshot (not a claim; only doc-keeper edits this)
Copied from `team.py status` on 2026-09-27. Claim through the tool, never from this list.
1. ready plan tasks: T48b (owner: `python -m tradepartner.cli_record paper <SYMBOL>` on a flat paper account in market hours, a non-fractionable symbol)
2. unclaimed issues: #258 (A→B→A facts, unsized), #183 (owner-run Probe 2 same-day live case), #33 (broker account query; delivered in substance by T46b/#280, closable)
3. parked PRs: none

## Blocked
- Every other plan task waits on its dependencies (board: T19, T22, T23, T42, T45, T45b, T48c, T51 to T67, T69, T69b, T70b, T71, T71b).
- T70 waits on Probe 3 (#182) results; T48c and its dependents wait on the owner's T48b recording.

## Decisions needed from owner
- #281 strategy-lab questions 2, 8 and 11 (ordering against T45b, dropping the shadow-paper replay, family lineage); then Fable drafts the Phase 3 spec sentence amendments and a superseding cadence ADR 0012 as separate PRs.
- A session day for owner-run probes #182 (Probe 3, Mon to Fri 2026-09-28 to 10-02, submit 09:00 to 09:15 ET, collect after 09:46) and #183.
- T48b paper recording run (owner keys, market hours, flat account).
- Close #33 (delivered by T46b, #280).
- Prune the retired team directories (otegra, placid, bitfly).
- Sign-off on H1's recorded survivorship gap (`gap_signoff` in the registry) before Phase 4 paper start, per ADR 0009 and ADR 0003 rule 8.
- GitHub Pro decision for the server-side main ruleset still open.
- Before Phase 6 only: account type, employer compliance check

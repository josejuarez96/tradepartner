# Status

**Updated:** 2026-09-25 · **Phase:** 2, Data foundation · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

## Done
Plus the entries in `docs/status.d/` not folded in yet: `uv run python scripts/fragments.py show` (#70).
- Phases 0 and 1: scaffold, ways of working, charter, ADRs 0001–0006, Phase 2 spec and plan; v0.1.0 tagged 2026-09-24 (git history has the detail)
- Phase 2 T1: config, dependencies, calendar (PR #17)
- Phase 2 T4: store schema, db layer, shared test loader (PR #20)
- Phase 2 T2: raw-fetch clients (Alpaca, EDGAR) and fixture recorder, with secret/email/User-Agent scrub (PR #21)
- #36 Multi-team orchestration: `scripts/team.py` (register, claim, release, status, check-claims), [teams.md](ways-of-working/teams.md), plan chains, model tiers, CI `claims` job (PR #37). Duplicate T5/T20 work from two unsynchronised windows cleaned up (#34 → #31, #26 → #27, both parked)
- Phase 2 T20: `Broker` interface and in-memory fake broker, no risk logic (PR #27)
- Phase 2 T5: fixture-universe generator and CSVs covering every spec req 13 case (PR #31)
- #30 Shared tz-aware UTC check `tradepartner.timeutil.ensure_tz_aware_utc` used by `store.db` and `adapters.broker` (PR #41)
- #43 `insert_row` binds the UTC-normalized `TIMESTAMPTZ` value (one canonical form); `ensure_tz_aware_utc` raises `ValueError` (not `OverflowError`) near `datetime.min`/`datetime.max`
- #53 Plan amendment: T21a dashboard shell split out of T21, depends only on T4; T21 now depends on T18 and T21a, T19 also on T21a (PR #62)
- #57 Broker-level tests: a UTC-overflowing timestamp raises `ValueError` from `Order`/`Fill` and fails closed in `FakeBroker.submit`/`simulate_fill` (PR #63). Clock-failure exception design split to #64
- #46 Research G8: price-vendor comparison for the Phase 3 vendor ADR, delisted coverage graded claimed/absent, report marked INCOMPLETE with open items ([report](research/2026-09-25-price-vendors.md))
- Phase 2 T21a: Streamlit dashboard shell with one read-only connection per render, no-store / busy / unreadable states and a health-page placeholder (#66, PR #67)
- #55 Teams picking order: step 3 now covers any unclaimed sized issue (size:S, type:research with an owner-approved brief via `researcher`, type:decision/type:docs drafts via Fable and `spec-critic`); unsized issues are not claimable (PR #56)
- Phase 2 T6: as-of primitives and truncation-invariance harness (PR #69)
- #48 Research G3: independent evidence on trend timing graded MIXED, at best marginal; the closest-transfer out-of-sample, net-of-cost test (Zakamulin 2014, S&P 1930–2012, 0.5% one-way) finds at most a +7% Sharpe gain with lower return and no significance (PR #82, [report](research/2026-09-25-trend-timing.md))
- #49 Research G4: combining published anomaly signals, graded MIXED; relative net-of-cost benefit over a single signal, absolute large/mid-cap edge near zero since 2003, long-only untested; report INCOMPLETE (abstract-level sources) ([report](research/2026-09-25-g4-signal-combination.md))
- #64 ADR 0007: clock faults in the broker path take the halt path, never the rejection path; rejection is an allowlist, `ClockError` (not a `ValueError`) plus a pre-submit clock check, implemented in the Phase 4 wrapper task (PR #76)
- #65 Owner cockpit: `uv run python scripts/cockpit.py` (or `--loop N`) renders `data/cockpit/cockpit.html` showing teams, activity, tokens, claims, roadmap and plan by chain (PR #68)
- #70 Per-PR STATUS/CHANGELOG fragments (`scripts/fragments.py`), `scripts/ready_pr.py` and the `/ready-pr` skill: parallel PRs stop conflicting on shared docs; one command readies a PR (PR #71)
- #72 Dividend prior-close fallback bounded by `adjust.max_prior_close_gap_sessions` (default 5 XNYS sessions); unapplied dividends reported by `dropped_dividends_as_of` (PR #79)
- #73 agents.md: "Parallelism inside a team", what a window runs in parallel inside its claimed scope and what stays one-at-a-time (PR #74)
- Phase 2 T7: `PriceSource` interface, price-side timing rules and fixture adapter that refuses fixture rows breaking a timing rule (#75, PR #81). Early action stamps are not checkable from the stored layout, see #83
- #78 `ready_pr.py` runs pytest locally only when the diff touches src/, tests/, scripts/ or dependencies; `--tests` / `--no-tests` override; CI stays the gate (PR #80)
- Phase 2 T3 (owner): fixtures recorded from Alpaca and EDGAR with real keys and scrubbed; free plan confirmed to return SIP history, so `alpaca.historical_feed=sip` and `universe.liquidity_rule_enabled=true`; `fill_price` stays `close`; source terms and backfill estimate in [research](research/2026-09-25-free-data-terms.md) (#84, PR #87)

## Teams
New session: `uv run python scripts/team.py start <name>`, then work only in the directory it prints (`../tradepartner-teams/<name>`). (#40)
Live board: `uv run python scripts/team.py status`. Snapshot 2026-09-25 06:50 UTC: active claims are `atlas` (main checkout; #90 this fold), `diomedes` (#47 G1 research, PR #61 ready but conflicting: needs `/ready-pr`), `omega` (T8, #77, PR #85 draft), `weaver` (#89 cockpit work map). Retired today after finishing: artemis, banshee, blackhole, centurion, cloudchaser, creed, ephemeral, finneas, floxer, gamma, geneve, guilo, lyon, nueron, orion, riptide, spear, utopia.

## In progress
- **Owner cleanup (agents are blocked from this):** remove stopped worktrees under `.claude/worktrees/` (agent-a02011651cb95bd2a, agent-a0567f206c3efef33, agent-a1e3ae085a0154b3a, agent-aee102ec0271d2d42, beta, creed, lyon, orion, orion-2), the retired team directories under `~/Projects/tradepartner-teams/` (artemis, banshee, blackhole, centurion, cloudchaser, creed, ephemeral, finneas, floxer, gamma, geneve, guilo, lyon, nueron, orion, riptide, spear, utopia, cockpit-loop), and merged remote branches that auto-delete missed: chore/30-share-tz-aware-utc, chore/43-chore-store-canonical-utc, chore/44-lowercase-team-names, chore/57-test-exec-broker-level, chore/65-cockpit, chore/70-pr-fragments-and-ready, chore/78-ready-pr-conditional-tests, docs/53-plan-amendment-split-the, docs/55-picking-order, docs/58-picking-order, docs/64-clock-failures-halt-path, docs/73-parallel-in-scope, feat/24-fake-broker, feat/25-fixture-universe, feat/39-as-of-primitives-and, feat/66-dashboard-shell, feat/75-pricesource-interface-and-fixture, feat/84-record-fixtures-and-resolve, fix/72-dividend-prior-close-gap, research/46-research-g8-survivorship-bias, research/48-trend-timing, research/49-g4-signal-combination
- **T8** (#77, omega, PR #85 draft): the chain head; T8b then T9 open when it merges.

## Ready frontier snapshot (not a claim; only doc-keeper edits this)
Copied from `team.py status` on 2026-09-25. Claim through the tool, never from this list.
1. ready plan tasks: none (T8 in progress is the gate; after it: T8b → T9, then T10 and T11; T12 after T7 and T8b)
2. unclaimed issues: #86 (research, size:S), #83 (feat), #52 (LLM role ADR, decision, size:M, Fable), #51 (Phase 3 spec+plan, size:L, Fable), #50 (research G7, size:M), #38 (feat), #35 (size:S), #33 (Phase 4 idea, leave it)
3. parked PRs: none
4. remaining handoff §12 decisions become ADRs in the phase that needs them; G2, G5, G6 research not yet briefed
5. GitHub Pro decision for the server-side main ruleset still open

## Blocked
- none

## Decisions needed from owner
- #52 LLM role (none / advisory / veto): overdue from Phase 1 exit criteria
- #35 snapshot_static timing rule: T8 is built on the strict `known_at <= T` reading; confirm before PR #85 is readied
- Before Phase 6 only: account type, employer compliance check

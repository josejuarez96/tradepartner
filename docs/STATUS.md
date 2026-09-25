# Status

**Updated:** 2026-09-25 · **Phase:** 2, Data foundation (15/25) and 3, Backtest (6/23) in parallel · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

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
- #35 Owner decision: `snapshot_static` rows get no as-of or truncation exemption (strict `known_at`, as merged in #69); tests at a 2018 T and around PRE9's `known_at`, spec and `asof.py` record the rule
- #38 Broker symbols canonicalized to upper-case ASCII in `OrderRequest`/`Order`/`Fill`/`Position`, so `aapl` and `AAPL` net as one position; non-ASCII symbols rejected
- #47 Research G1: post-2010 long-only momentum, net of costs. Graded MIXED; the prior for net excess over the benchmark is centred near 0 (PR #61, [report](research/2026-09-25-g1-momentum-post-2010.md))
- #50 Research G7: wash-sale and holding-period rules checked against IRS primary sources (§1091, §1222/§1223, Reg. 1.1091-1, Rev. Rul. 2008-5, Pub 550, Form 8949 and 1099-B instructions); IRA repurchase disallows the loss with no basis step-up; brokers report only same-account, same-CUSIP wash sales, so the system keeps its own cross-account lot ledger (PR #91, [report](research/2026-09-25-g7-wash-sales.md))
- #51 Phase 3 spec and plan: backtest engine, cost model, locked holdout, trial registry, deflated Sharpe, `bt` oracle, backtest page and trial-registry view ([spec](specs/backtest.md), [plan](plans/backtest.md))
- #52 ADR 0008 (Proposed): no LLM in the runtime until Phase 5; advisory memo is the only authority without a new ADR (batch job, owner-only reader, probability scored never consumed, overrides linked to memos); veto needs a pre-registered test over the forward record (PR #96)
- Phase 2 T8: `FilingSource` interface, fixture adapter, security master core (`build_master`, `securities_as_of`) (#77, PR #85)
- #83 `corporate_actions.announced_at` (nullable, schema v2): a first-seen action is stamped exactly at its announcement capped at the close before ex-date, else exactly at that close; revisions carry `announced_at` unchanged; the fixture adapter now refuses an earlier stamp with no announcement (look-ahead). Date-only announcements count from the next session close. Ex-date re-dating split to #108
- #86 Research: pre-open Alpaca fractional orders fill from Alpaca's inventory at the NBBO, never in the auction (Tier 1); whole-share auction participation not documented; paper has no auction. Owner probe protocols for corporate-actions depth, missing-bar share and a paper open probe; measurements moved to #101 (PR #100, [report](research/2026-09-25-alpaca-open-and-depth.md))
- #89 Cockpit work map: the page now opens with distance to the MVP per phase (plain words, exit criteria as a checklist, unplanned phases point at their spec issue), a dependency graph of phases, plan tasks, issues, research, ADRs, specs and plans (click for what/why/needs/feeds), research in flight, and what waits on the owner; words come from the new `docs/work-map.toml` (PR #92)
- #94 Research G4 pass 2: full-text read of DeMiguel et al., Novy-Marx, Chen & Velikov, plus Fitzgibbons et al. 2017 and McLean & Pontiff 2016. Still MIXED, now COMPLETE: combining beats a single signal out of sample and net of costs for long-short portfolios; the long-only gain is not significant, and the large/mid-cap edge is near zero since 2003 ([report](research/2026-09-25-g4-signal-combination.md))
- #101 Probe 1 (owner keys, run by agent with owner permission): Alpaca corporate actions complete 2016–2025 for every ground-truth split, reverse split, spin-off and dividend, so ADR 0003's 2016 adjustment horizon stands; the date window filters on `process_date`, and no year carries an announcement date (bears on #83). Probes 2 and 3 split to #106 ([report](research/2026-09-25-alpaca-open-and-depth.md))
- #102 Plan amendment: T13 `universe_as_of`, T15 `survivorship_gap` and T8b `listings_as_of` take an optional `settings: Settings | None` (default: loaded config) for every `universe.*`, `master.*` and `gap.*` value, passed down to callees, with override tests; T9 read-time entry points likewise; unblocks Phase 3 T38 (PR #103)
- #104 Alpaca delisted-symbol probe (owner keys, run by agent with owner permission): SIP daily bars returned to the last session for 8 of 8 delisted names (5 acquired, 3 failed); bars start 2016-01-04; zero-volume placeholder bar after a delisting (ATVI); FB→META history served under both symbols; failed names 404 on the assets endpoint. G8's Alpaca grade "Absent" superseded ([report](research/2026-09-25-alpaca-delisted-bars.md))
- Phase 2 T8b: Forms 25/25-NSE resolved to one share class (title up to the comma, else the single plain-common class; warrant/preferred filings never end the common; unresolved ones returned as `unmatched`); `listing_ends_as_of` derives listed/delisted/transferred and the end session from rows known at T only (PR #110)
- #113 Owner accepted ADR 0008 (LLM role) as written: strategy code makes the recommendations, the LLM is at most an advisory memo to the owner over point-in-time inputs; wider authority stays open for a later ADR (point 9). Closes the #52 owner decision
- Phase 3 T30: backtest config (`hypotheses`, `strategy`, `costs`, `holdout` null by default, `backtest`, `metrics`), `empyrical-reloaded==0.5.12` runtime and `bt==1.2.3` dev, AST guard against `bt`/`ffn`/`yfinance` under `src/`, ADR 0004 amendment (#116, PR #120)
- Phase 3 T31: trial-registry schema at version 3 (#83 took version 2 for `announced_at`), eight `REGISTRY_TABLE_NAMES` tables disjoint from `TABLE_NAMES`, additive migration from version 2 on any write open, `RegistryNotInitialised` on a read-only open of a version-2 store (#117, PR #119)
- Phase 2 T9: security-type classification (`store/classify.py`): per-class type from fund/F-6/foreign forms, SIC 6770, cover-page titles and pre-2019 ticker suffixes, one row per change known when its evidence was, unclassifiable bucket, `classifications_as_of` (#121, PR #122)
- Phase 3 T33: cost model `backtest/costs.py` (`trade_cost`, `buy_notional_after_costs` keeping post-cost cash ≥ 0, `sensitivity_levels`), checked against a hand-computed three-trade fixture (#123, PR #124)
- Phase 2 T10: no-look-ahead suite (`tests/lookahead/test_suite.py`) with five named checks over an adapter's ingest history; the fixture price adapter passes all five and `BrokenPriceSource` is caught for each violation by exactly its named check (#125)
- Phase 3 T32: `backtest/metrics.py`, every req 7 key via `empyrical` where it exists, PSR and deflated Sharpe on the `raw` and `excess_spy` bases (spec reference values reproduced to 1e-6), red flag (#127, PR #128)
- Phase 2 T11: EDGAR parsers (`adapters/edgar.py`) over T3's recordings: submissions and quarterly index with acceptance times (never from filing dates; unknown ones reported as unstamped), tickers snapshot, company facts, SGML header SIC (Eastern to UTC), cover-page classes and per-class shares via `edgartools`, Forms 25/25-NSE; exchanges normalized (#129, PR #131)
- Phase 3 T31b: `store.registry` API: `TrialHandle` issued only by `open_trial`; hypotheses registered with canonical params and hash (identical re-registration returns the existing row); oracle family and synthetic trials refused on the real store; result rows via `close_trial`/`write_result` (`failed` when the store changed during the run); detail-row writers; owner decisions; `family_sharpes` N and per-basis V inputs over latest-per-pair; holdout spends; `list_trials` with `unfinished` (#130)
- Phase 3 T34: 12-1 momentum signal (`backtest/signals.py`, excluded names counted, nothing after `t_session` read) and portfolio construction (`backtest/portfolio.py`: top fraction equal weight, ties by `security_id`, drifted weights, trades) (#133, PR #134)
- Phase 2 T13: `universe_as_of(conn, t, settings)` applies ADR 0006 rules 1–8 in order from rows known at T, reports each exclusion under its first failing rule with a reason, sums dual-class cap per company, adjusts shares only for splits with ex-date ≤ T, and records enabled rules and settings; truncation-invariant at every fixture `known_at` (#135)

## Teams
New session: `uv run python scripts/team.py start <name>`, then work only in the directory it prints (`../tradepartner-teams/<name>`). (#40)
Live board: `uv run python scripts/team.py status`. Snapshot 2026-09-25 14:05 UTC: active claims are `atlas` (main checkout; #141 this fold), `klous` (T12, #137, PR #139 draft), `emory` (T35, #138), `bitfly` (T37, #140). Forty team labels exist; every other team has finished and released. Retired team directories under `~/Projects/tradepartner-teams/` may be removed by the owner (see In progress).

## In progress
- **Owner cleanup (agents are blocked from this):** stale worktrees under `.claude/worktrees/` (agent-a02011651cb95bd2a, agent-a0567f206c3efef33, agent-a1e3ae085a0154b3a, agent-aee102ec0271d2d42, beta, creed, lyon, orion, orion-2); retired team directories under `~/Projects/tradepartner-teams/` (all except altrux, bitfly, emory, klous and, until the cockpit loop is restarted from the main checkout per the #65 comment, cockpit-loop); merged remote branches that auto-delete missed: every `origin/*` branch other than `main` and the three open PR branches (`feat/137-alpaca-parsers`, `research/106-data-owner-run-alpaca`, `fix/108-corporate-action-re-dated`), about 50 of them, plus the three `spike/*` branches (spikes are never merged; delete after their research reports landed: #101, #104, #106).
- **Phase 2 critical path:** T14 and T15 (ready, unclaimed) then T16 ingest needs T11 and T12 (T12 in progress, #139); T17 → T18 → T19 → T21, T22, T23 follow.
- **Phase 3 critical path:** T35 (emory) and T37 (bitfly) in progress; T36 ready; T37b/T37c engine open when T31b, T33, T34 and T37 are merged; T35b (first hypothesis) waits on the owner's answers to spec open questions 1, 2, 8 and 10.

## Ready frontier snapshot (not a claim; only doc-keeper edits this)
Copied from `team.py status` on 2026-09-25 14:05 UTC. Claim through the tool, never from this list.
1. ready plan tasks: T29 (owner, price-vendor ADR), T36, T43, T14, T15
2. unclaimed issues: #108 (fix, size:M, parked PR #111 green, re-claim to continue), #106 (owner-run Alpaca probes, research), #33 (Phase 4 idea, leave it)
3. parked PRs: #111
4. G2, G5, G6 research not yet briefed; remaining handoff §12 decisions become ADRs in the phase that needs them
5. GitHub Pro decision for the server-side main ruleset still open

## Blocked
- none

## Decisions needed from owner
- T29 price-vendor ADR (owner task; G8 research is the input; due at Phase 3 start, gates the Phase 3 close-out chain only)
- Phase 3 spec open questions 1, 2, 8 and 10, answered on the T35b issue once it exists; the first hypothesis cannot be written until then
- Rebalance cadence: the owner wants to test weekly rebalancing later. Proposed 2026-09-25: amend the Phase 3 spec so the rebalance schedule is a hypothesis parameter (month-end or week-end sessions; metrics annualised from the schedule) before the engine tasks T37b/T37c are claimed. Not yet decided.
- Before Phase 6 only: account type, employer compliance check

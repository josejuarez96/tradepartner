# Plan: Backtest engine and trial registry (Phase 3)

**Spec:** [specs/backtest.md](../specs/backtest.md)  ·  **Status:** Draft

## Approach (short)

Build the pure parts first, then the store-facing parts, then the checks, then the surfaces. Config and dependencies; the registry schema and its append-only API; metrics with deflated Sharpe, the cost model, the signal and portfolio rules, the holdout decision, the valuation and schedule helpers: all pure functions over synthetic inputs, none of which needs Phase 2 to finish. The engine loop is written against a `DataProvider` protocol with a fake provider in tests, so it too starts early. Only the store-backed provider needs `universe_as_of` (T13), `survivorship_gap` (T15) and read-time delistings (T8b); everything after it (run orchestration, invariance suite, oracle, CLI) waits on that one task; the results writer (T39b) does not, so it lands before orchestration and orchestration depends on it. The two pages read registry tables and start as soon as the registry API merges. Two owner tasks bracket the phase: the price-vendor ADR (T29) and the real-store run (T45b); agents never enter the main checkout (CLAUDE.md rule 9), so no agent task touches the owner's store.

**What may start while Phase 2 runs.** Any task whose dependencies are all ticked on `origin/main` is claimable the moment this plan merges (teams.md): T29 (owner), T30 (depends on T1) and T31 (on T4) immediately; T31b, T32, T33, T34, T37 next; T35, T36, T43 once T31b and T30 merge; T37b once T31b, T33, T34 and T37 merge; T39b once T31b, T32 and T37c merge. T38 (which needs only T37's protocol and T31b's handle, not the engine) is the gate to the Phase 2 chain; T42 also waits on the Phase 2 CLI (T19). The H1 hypothesis file is its own docs task (T35b), where the owner answers spec open questions 1, 2, 8 and 10; only T45b depends on it. Starting early is deliberate: pure functions over synthetic inputs test the same whether or not real data exists, and the engine's correctness does not depend on the vendor decision. Spec open question 9 puts that choice to the owner; merging this plan records it, and `DEPENDS_RE` enforces the gate because T29 sits in T45's and T45b's dependencies.

**Price-vendor ADR (T29, owner).** No build task depends on it and none writes a vendor adapter (out of scope). If the ADR chooses a vendor, its adapter is a new plan task added by amendment before T45b runs.

Design choices: engine as a rebalance loop over a `t`-parameterised provider, positions carried as dollar values priced within one adjusted-as-of-`t` frame per step ([ADR 0003](../decisions/0003-data-adapters-local-first.md) rule 2, [ADR 0006](../decisions/0006-universe-and-cadence.md)); a short-lived read-only connection per step so a run never holds the ingest job's lock; our own engine with `bt` as a test-only oracle over a stitched frame, at a tolerance pinned in the test file ([ADR 0004](../decisions/0004-tooling-adopt-avoid.md); the roadmap's "configured tolerance" means that constant, not a `Settings` key); registry tables in the DuckDB store, append-only, trial handle before data ([ADR 0005](../decisions/0005-objective-benchmark-stop-criteria.md)), verified through a registry connection separate from the data connection so the look-ahead harness's fact-table-only store still works; frozen parameters per hypothesis including the holdout window and gap threshold; deflated Sharpe hand-written on two bases and tested against reference values. **Metrics library:** ADR 0004 leaves the choice between `empyrical-reloaded` and `quantstats` to this plan. This plan picks **`empyrical-reloaded`** (0.5.12, pinned): pure functions over series with no plotting stack; on PyPI (checked 2026-09-25) its runtime requirements are numpy, pandas, scipy, bottleneck and peewee, with `yfinance` only as an optional extra, whereas `quantstats` requires `yfinance`, matplotlib and seaborn outright and ADR 0004 avoids `yfinance` outside spikes. `bt==1.2.3` goes in the dev dependency group only; it pulls `ffn`, which pulls `yfinance` and `scikit-learn`, into the dev environment and nowhere else. An AST test keeps `bt`, `ffn` and `yfinance` out of `src/`, and the autouse no-network fixture keeps them off the network.

**All dependencies are added in T30** (`empyrical-reloaded` runtime, exact pin; `bt` dev, exact pin) so no later task touches `pyproject.toml` or `uv.lock`. `uv.lock` lines never count toward the ~400-line budget. T30 also appends a dated amendment note to ADR 0004 recording the metrics choice and the oracle's fill-session-only comparison under `open` fills.

**Phase 2 interface note (required follow-up).** T38 passes the trial's frozen `Settings` into every as-of call. `adjusted_prices_as_of` already takes `settings`; `universe_as_of` (T13) and `survivorship_gap` (T15) must accept one too. This plan does not edit `data-foundation.md`; issue #102 asks that T13 and T15 land with a `settings` parameter, or a `size:S` follow-up adds it before T38 starts.

**Amendment 2026-09-25 (#201, owner decisions).** T40's suite cuts the store at close(end), and the last step of a run legitimately reads there, so an engine that fills F_k from targets planned at read_time(T_{k+1}) passes truncation and prefix invariance, and the revision case only asserts that the run to T_{i+1} changes, which a late plan read satisfies too. The owner chose to add a check rather than decline it: spec req 13 now requires that a plan read at a later rebalance close is detected when it changes a compared plan field, T40b tests it, and T45 waits for it, since its evidence line claims the invariance suite. The same day the owner decided T_0's coverage: windowed runs `run(start=T_k, end=T_{k+1})` on a test-only benchmark-exempt cut (benchmark bars kept past the cut, safe because no plan field reads a benchmark), which reach the first-plan call site at every rebalance, T_0 included, at a fraction of the cost of full replays.

Package layout: `src/tradepartner/backtest/{__init__,metrics,costs,signals,portfolio,hypothesis,holdout,provider,schedule,valuation,fills,engine,store_provider,run,results}.py`, `src/tradepartner/store/registry.py`, `src/tradepartner/dashboard/{backtest_page,trials_page}.py`, `docs/hypotheses/`, `docs/templates/hypothesis.md`; tests under `tests/backtest/`, `tests/lookahead/test_backtest_invariance.py`, `tests/oracle/test_bt_oracle.py`, `tests/dashboard/`, with the temp-file fixture store (`fixture_store_path`) added to the root `tests/conftest.py` (T39b) so `tests/oracle/` and `tests/backtest/` both see it, and a fixture hypothesis file under `tests/fixtures/hypotheses/`.

## Tasks

Each task = one branch = one PR (~≤400 lines; `uv.lock` excluded). Tasks with no shared files may run in parallel. Task ids start at T29 because ids are global across plans. "Owner" tasks need the owner's machine, keys or budget decision and are never run by `implementer`.

- [x] **T29 (owner): Price-vendor ADR.** (#240, PR #241) · Files: `docs/decisions/0009-price-vendor.md`, `docs/charter.md` · Depends on: n/a
- [x] **T30: Phase 3 config and dependencies.** (#116, PR #120) · Files: `src/tradepartner/config.py`, `pyproject.toml`, `uv.lock`, `docs/decisions/0004-tooling-adopt-avoid.md` · Depends on: T1
- [x] **T31: Registry schema and migration.** (#117, PR #119) · Files: `src/tradepartner/store/schema.py` · Depends on: T4
- [x] **T31b: Registry API and trial handle.** (#130, PR #132) · Files: `src/tradepartner/store/registry.py`, `docs/research/trial-registry.md` · Depends on: T31
- [x] **T32: Metrics and deflated Sharpe.** (#127, PR #128) · Files: `src/tradepartner/backtest/{__init__,metrics}.py` · Depends on: T30
- [x] **T33: Cost model.** (#123, PR #124) · Files: `src/tradepartner/backtest/costs.py` · Depends on: T30
- [x] **T34: Momentum signal and portfolio construction.** (#133, PR #134) · Files: `src/tradepartner/backtest/signals.py`, `src/tradepartner/backtest/portfolio.py` · Depends on: T30
- [x] **T35: Hypothesis template and pre-registration.** (#138, PR #142) · Files: `docs/templates/hypothesis.md`, `docs/README.md`, `tests/fixtures/hypotheses/fixture-momentum.md`, `src/tradepartner/backtest/hypothesis.py` · Depends on: T30, T31b
- [x] **T35b: H1 hypothesis file.** (#156, PR #160) · Files: `docs/hypotheses/h1-momentum-12-1.md` · Depends on: T35
- [x] **T36: Holdout, window and gap-gate decisions.** (#145, PR #149) · Files: `src/tradepartner/backtest/holdout.py` · Depends on: T30, T31b
- [x] **T37: Provider protocol, fake provider, schedule and valuation.** (#140, PR #148) · Files: `src/tradepartner/backtest/provider.py`, `src/tradepartner/backtest/schedule.py`, `src/tradepartner/backtest/valuation.py`, `tests/backtest/fake_provider.py` · Depends on: T30
- [x] **T37b: Engine loop, fills, costs and cash.** (#155, PR #159) · Files: `src/tradepartner/backtest/fills.py`, `src/tradepartner/backtest/engine.py` · Depends on: T31b, T33, T34, T37
- [x] **T37c: Engine exits and benchmarks.** (#187, PR #190) · Files: `src/tradepartner/backtest/engine.py` · Depends on: T37b
- [x] **T38: Store-backed provider.** (#170, PR #175) · Files: `src/tradepartner/backtest/store_provider.py` · Depends on: T8b, T13, T15, T31b, T37
- [x] **T39b: Results, metrics and deflated Sharpe writes.** (#203, PR #206) · Files: `src/tradepartner/backtest/results.py`, `tests/conftest.py` · Depends on: T31b, T32, T37c
- [x] **T39: Run orchestration and refusals.** (#221, PR #223) · Files: `src/tradepartner/backtest/run.py` · Depends on: T31b, T35, T36, T38, T39b
- [x] **T40: No-look-ahead suite for the full run.** (#191, PR #200) · Files: `tests/lookahead/test_backtest_invariance.py` · Depends on: T38
- [x] **T40b: Plan-read timing check.** (#214, PR #215) · Files: `tests/lookahead/test_backtest_plan_timing.py` · Depends on: T40
- [x] **T41: `bt` oracle.** (#236, PR #239) · Files: `tests/oracle/{__init__,test_bt_oracle}.py` · Depends on: T39
- [x] **T42: CLI: `backtest`, `hypothesis register`, `trials`, `decision`.** (#309, PR #319) · Files: `src/tradepartner/cli.py` · Depends on: T19, T39
- [x] **T43: Backtest page.** (#147, PR #150) · Files: `src/tradepartner/dashboard/backtest_page.py`, `src/tradepartner/dashboard/app.py` · Depends on: T21a, T31b
- [x] **T44: Trial-registry view.** (#161, PR #162) · Files: `src/tradepartner/dashboard/trials_page.py`, `src/tradepartner/dashboard/app.py` · Depends on: T43
- [x] **T45: `backtest-runner` agent and docs close-out.** (#323, PR #328) · Files: `.claude/agents/backtest-runner.md`, `docs/ways-of-working/agents.md` · Depends on: T29, T40, T40b, T41, T42, T44
- [x] **T45b (owner): Real-store run of H1 and phase close.** (#503, PR #1020) · Files: `docs/STATUS.md`, `CHANGELOG.md`, `docs/plans/backtest.md` · Depends on: T29, T35b, T45 · Evidence: trial 2 PASS (DSR 0.7262), trial 1 FAIL on #853 disclosed, quant-auditor verdicts in PR

**Parallel lanes** (separate worktrees, non-overlapping files): T29 alone; T30 → {T32, T33, T34, T37}; T31 → T31b → {T35, T36, T43}; T35 → T35b; {T31b, T33, T34, T37} → T37b → T37c; {T8b, T13, T15, T31b, T37} → T38 → T40 → T40b; {T31b, T32, T37c} → T39b; {T31b, T35, T36, T38, T39b} → T39 → {T41, T42}; T19 → T42; T43 → T44; {T29, T40, T40b, T41, T42, T44} → T45; {T29, T35b, T45} → T45b.

## Chains (for team claims)

Dependent tasks one team should keep, in order. A chain is a preference, not a lock: every task is still claimed one at a time with `scripts/team.py claim` ([teams.md](../ways-of-working/teams.md)). The `pure` and `registry` chains open the moment this plan merges and run alongside Phase 2; the `store` chain waits for the Phase 2 `rules` chain (T13, T15) and `master` chain (T8b); `cli` also waits for the Phase 2 `pipeline` chain (T19). Merge chain heads first.

| Chain | Tasks | Starts when |
|---|---|---|
| vendor (owner) | T29 | this plan merges; due at Phase 3 start (ADR 0003 rule 8) |
| pure | T30 → T32 → T33 → T34 → T37 | this plan merges (T1 is done) |
| registry | T31 → T31b → T35 → T36 | this plan merges (T4 is done; T35 and T36 also need T30) |
| hypothesis (docs) | T35b | T35 merged; the owner answers spec open questions 1, 2, 8 and 10 on its issue |
| engine | T37b → T37c → T39b | T31b, T33, T34 and T37 merged (T39b also needs T32) |
| store | T38 → T39 | T31b and T37 merged, plus T8b, T13, T15 from Phase 2 (T39 also waits for T35, T36 and T39b) |
| checks | T40 → T40b, T41 (parallel) | T38 merged for T40; T40 merged for T40b; T39 merged for T41 |
| cli | T42 | T19 (Phase 2) and T39 merged |
| ux | T43 → T44 | T31b merged (T21a is done) |
| close-out | T45 → T45b (owner) | every other chain merged, T29 and T35b included |
| fixes | open `size:S` issues with no `team:` label | any time |

## Verification

End to end (refreshed by T45 against the merged CLI):
1. Any checkout (T45): `uv run pytest` green with no `.env` and no network, including `tests/lookahead/test_backtest_invariance.py` and `tests/oracle/test_bt_oracle.py`. A team window may also have `backtest-runner` run a hypothesis file on a temp copy of the fixture store, inside the fixture's 2018-01-31 to 2020-06-30 window; that proves the pipeline, never the strategy, and its trials never reach the real registry.
2. Owner, main checkout (T45b, after T35b merged): `uv run tradepartner hypothesis register docs/hypotheses/h1-momentum-12-1.md` prints the hypothesis id, the full frozen set and its hash; that command checks the real store's schema and migrates it if needed (current version 5 since Phase 4's T49; `ingest` usually got there first).
3. Owner: `uv run tradepartner backtest h1-momentum-12-1` exits 0 and prints the trial id, the metrics table at the frozen base cost, the strategy's metrics per cost level, DSR and DSR over SPY with their basis and N, the gap maxima and the flags; V and SR* for both bases are in `trial_results`, so a direct DuckDB query of `trials`, `trial_results` and `trial_rebalances` is pasted alongside.
4. Owner: `uv run tradepartner backtest h1-momentum-12-1 --end <a date inside the holdout>` exits 2 and `uv run tradepartner trials` shows the `refused_holdout` row; `--end <a date after holdout.end>` exits 2 with `refused_window`; `HOLDOUT__START=2030-01-01` in the environment changes nothing.
5. Owner: `uv run tradepartner dashboard` shows the trial on the backtest page (stored and recomputed DSR) and in the trial-registry view (screenshots).
6. Owner, once the recorded gap is accepted: `uv run tradepartner decision gap-signoff --trial <id> --reason "<why>"` appends the `gap_signoff` owner decision (ADR 0003 rule 8), the Phase 4 entry gate.
7. `quant-auditor` report on the H1 run attached (T45b).

## Rollback

Registry tables are additive: the fact tables are never touched. To roll back T31 on a store that already reached version 2: drop the eight `REGISTRY_TABLE_NAMES` tables, then `DELETE FROM schema_version WHERE version = 2` by hand, because a reverted `init_schema` raises `SchemaVersionError` on any version other than 1 (`store/schema.py`); write that command in the revert PR. Dependencies are removed by reverting T30. Hypothesis files are docs. No scheduled job is added in this phase.

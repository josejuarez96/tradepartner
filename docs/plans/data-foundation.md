# Plan: Data foundation (Phase 2)

**Spec:** [specs/data-foundation.md](../specs/data-foundation.md)  ·  **Status:** Draft

## Approach (short)

Build inside-out (the filing index is always scanned over full history; `--since` limits only bars, actions and facts): config and calendar; raw-fetch clients and the recorder (so the owner can record fixtures early); the store and its as-of API with the truncation-invariance harness; the fixture universe; fixture adapters and the security master; the no-look-ahead suite; parsers for the real sources over the owner's recordings; universe, gap, ingest, health, CLI, page, close-out. CI never touches the network: an autouse fixture disables sockets.

Design choices: DuckDB file, raw OHLCV, read-time adjustment ([ADR 0003](../decisions/0003-data-adapters-local-first.md), [0004](../decisions/0004-tooling-adopt-avoid.md)); universe rules as config with a guarded SIC setting ([ADR 0006](../decisions/0006-universe-and-cadence.md)); **Streamlit** for the page; **Typer** for the CLI; **httpx** for raw EDGAR fetches; `edgartools` only for cover-page iXBRL parsing. Typer, pydantic-settings and httpx are recorded here as additions to ADR 0004's adopt table via a dated amendment note appended in T1 (ADRs are append-only).

**All runtime dependencies are added in T1** so no later task touches `pyproject.toml`/`uv.lock` (except T19's `[project.scripts]`): `pydantic`, `pydantic-settings`, `exchange_calendars`, `typer`, `httpx`, `duckdb`, `pyarrow`, `polars`, `edgartools` (exact pin), `alpaca-py` (exact pin), `streamlit`. Per-module `ignore_missing_imports` set in T1.

Package layout: `src/tradepartner/{config.py, calendar.py, cli_record.py, store/{schema,db,asof,master,classify}.py, adapters/{__init__,prices,filings,broker,fixture_prices,fixture_filings,fake_broker,alpaca_raw,edgar_raw,alpaca_prices,edgar,edgar_source}.py, universe.py, gap.py, health.py, ingest.py, backfill.py, cli.py, dashboard/{__init__,app,health_page}.py}`; `tests/conftest.py` owns the fixture-store loader and the no-network fixture.

**Amendment 2026-09-25 (#216).** #174 found that the SEC Financial Statement and Notes data sets carry the cover facts and a per-filing SIC keyed by accession, so the cover-page scope of T11c is rebuilt on those files: T11c fetches and parses them (fetch, recorder and config split to T11g, #231) into unstamped per-CIK caches, T11d serves cover pages and SIC headers from them, stamped at read time from the submissions acceptance, with per-filing downloads only for accessions FSN does not yet hold and ranged SGML headers only for `edgar.header_forms` filings absent from FSN (registration forms from `edgar.header_first_year`, periodic forms and 8-K inside the lag window) and for lag-window FSN filings with a blank SIC, and moves the classification call site from `master.issuer_forms` to `edgar.header_forms`; T11e serves facts; T11f holds delistings, the failure policy for a filing that fails the same way on every run, and the owner's backfill measurement. Each is one team's work; T19 depends on T11f (*superseded by #261: the failure policy, the measurement and T19's dependency are T11h's*) and its `health --check` warns on a quarantined accession. Merging this amendment records owner decisions (1) (in T11c) and (2) (in T11f), and appends a dated note to the spec's security-master table.

**Amendment 2026-09-25 (#163).** No task built the EDGAR `FilingSource` adapter that spec req 6 requires: T2 delivered the raw client and T11 the parsers, and T16 is tested with fixture sources, so a real `tradepartner ingest` had no filing source. T11b and T11c add it in the parsers chain; T19 now depends on T11c (superseded by #216: T11f). Scale rules the design. The full index lists every filer (about a million CIKs), so the adapter keeps only issuer CIKs. T16 rebuilds full views on every run, so every run needs acceptance times for every kept row: stamped rows are therefore cached by accession and each run stamps only the accessions it has not seen, from the nightly `submissions.zip` when they belong to more than a threshold of CIKs (the backfill, or a long gap), per CIK otherwise, with a per-CIK top-up after a bulk pass because the zip is rebuilt at about 03:00 ET and never has that day's filings ([research](../research/2026-09-25-free-data-terms.md), runtime estimate). Cover pages and SGML headers for every issuer filing are hundreds of thousands of downloads; #174 (research) decides how both are sourced before T11c's clauses for them are built. T16 is unchanged but holds the write lock through its fetch pass, which #173 moves before T22; #172 writes the adapter's counts into the run row before T19. `scripts/team.py` reads only `T..` ids in "Depends on", so the issue gates written on T11c, T19 and T22 are not enforced by the ready list: read them before claiming.

## Tasks

Each task = one branch = one PR (~≤400 lines; generated fixture CSVs excluded). Tasks with no shared files may run in parallel. "Owner" tasks need the owner's keys and are not run by `implementer`.

- [x] **T1: Config, dependencies, calendar.** (#13, PR #17) · Files: `src/tradepartner/{config,calendar}.py`, `pyproject.toml`, `uv.lock`, `.env.example`, `docs/decisions/0004-tooling-adopt-avoid.md` · Depends on: n/a
- [x] **T2: Raw-fetch clients and the recorder.** (#18, PR #21) · Files: `src/tradepartner/adapters/{alpaca_raw,edgar_raw}.py`, `src/tradepartner/cli_record.py`, `tests/test_fixture_scrub.py` · Depends on: T1
- [x] **T3 (owner): Record fixtures and resolve source facts.** (#84, PR #87) · Files: `tests/fixtures/{alpaca,edgar}/*`, `docs/research/2026-09-25-free-data-terms.md`, `src/tradepartner/config.py`, `tests/test_config.py` · Depends on: T2
- [x] **T4: Store schema, db layer, shared test loader.** (#19, PR #20) · Files: `src/tradepartner/store/{__init__,schema,db}.py`, `src/tradepartner/adapters/__init__.py`, `tests/conftest.py` · Depends on: T1
- [x] **T5: Fixture universe (CSV) and generator.** (#22, PR #31) · Files: `scripts/make_fixture_universe.py`, `tests/fixtures/universe/*.csv`, `tests/fixtures/universe/README.md` · Depends on: T4
- [x] **T6: As-of primitives and truncation-invariance harness.** (#39, PR #69) · Files: `src/tradepartner/store/asof.py`, `tests/lookahead/{__init__,harness}.py` · Depends on: T5
- [x] **T7: `PriceSource` interface and fixture adapter.** (#75, PR #81) · Files: `src/tradepartner/adapters/{prices,fixture_prices}.py` · Depends on: T6
- [x] **T8: `FilingSource` interface, fixture adapter, security master core.** (#77, PR #85) · Files: `src/tradepartner/adapters/{filings,fixture_filings}.py`, `src/tradepartner/store/master.py` · Depends on: T6
- [x] **T8b: Delistings and transfers, derived at read time.** (#105, PR #110) · Files: `src/tradepartner/store/delistings.py` · Depends on: T8
- [x] **T9: Security-type classification.** (#121, PR #122) · Files: `src/tradepartner/store/classify.py` · Depends on: T8b
- [x] **T10: No-look-ahead suite and broken adapter.** (#125, PR #126) · Files: `tests/lookahead/{test_suite,broken_adapter}.py` · Depends on: T7, T8b
- [x] **T11: EDGAR parsers.** (#129, PR #131) · Files: `src/tradepartner/adapters/edgar.py` · Depends on: T3, T9
- [x] **T11b: EDGAR `FilingSource` adapter, index and snapshot.** (#184, PR #188) · Files: `src/tradepartner/adapters/edgar_source.py`, `src/tradepartner/adapters/edgar_raw.py`, `tests/adapters/test_edgar_raw_offline.py`, `src/tradepartner/adapters/filings.py`, `src/tradepartner/config.py`, `tests/test_config.py`, `tests/adapters/edgar_transport.py` · Depends on: T11
- [x] **T11g: EDGAR FSN data sets, fetch, recorder and config.** (#231, PR #232) · Files: `src/tradepartner/adapters/edgar_raw.py`, `src/tradepartner/cli_record.py`, `src/tradepartner/config.py`, `tests/test_config.py` · Depends on: T11b
- [x] **T11c: EDGAR FSN data sets, fetch, parse and record.** (#224, PR #242) · Files: `src/tradepartner/adapters/edgar_raw.py`, `src/tradepartner/adapters/edgar.py`, `src/tradepartner/adapters/edgar_source.py`, `src/tradepartner/ingest.py`, `tests/test_ingest.py`, `src/tradepartner/cli_record.py`, `src/tradepartner/config.py`, `tests/test_config.py` · Depends on: T11b, T11g
- [x] **T11d: EDGAR `FilingSource` adapter, cover pages and headers from FSN.** (#246, PR #249) · Files: `src/tradepartner/adapters/edgar_source.py`, `src/tradepartner/ingest.py`, `tests/test_ingest.py`, `src/tradepartner/config.py`, `tests/test_config.py`, `src/tradepartner/store/classify.py` · Depends on: T11c
- [x] **T11e: EDGAR `FilingSource` adapter, facts.** (#252, PR #253) · Files: `src/tradepartner/adapters/edgar_source.py` · Depends on: T11d
- [x] **T11f: EDGAR `FilingSource` adapter, delistings.** (#261, PR #262) · Files: `src/tradepartner/adapters/edgar_source.py`, `src/tradepartner/ingest.py`, `tests/test_ingest.py`, `src/tradepartner/store/delistings.py` · Depends on: T11e
- [x] **T11h: EDGAR failure policy and backfill measurement.** (#263, PR #275) · Files: `src/tradepartner/adapters/edgar_source.py`, `src/tradepartner/ingest.py`, `src/tradepartner/backfill.py`, `src/tradepartner/config.py`, `tests/test_config.py` · Depends on: T11f
- [x] **T12: Alpaca parsers.** (#137, PR #139) · Files: `src/tradepartner/adapters/alpaca_prices.py` · Depends on: T3, T7, T8b
- [x] **T13: Universe rules.** (#135, PR #136) · Files: `src/tradepartner/universe.py`, `tests/lookahead/test_universe_invariance.py` · Depends on: T9, T10
- [x] **T14: Universe config tests and literal check.** (#151, PR #153) · Files: `tests/test_universe_config.py`, `tests/test_no_literals.py` · Depends on: T13
- [x] **T15: Survivorship gap.** (#143, PR #152) · Files: `src/tradepartner/gap.py`, `tests/lookahead/test_gap_invariance.py` · Depends on: T13
- [x] **T16: Single-session ingest.** (#157, PR #164) · Files: `src/tradepartner/ingest.py` · Depends on: T11, T12
- [x] **T17: Backfill and resume.** (#169, PR #171) · Files: `src/tradepartner/backfill.py` · Depends on: T16
- [x] **T18: Health metrics.** (#186, PR #196) · Files: `src/tradepartner/health.py` · Depends on: T15, T17
- [x] **T19: CLI.** (#301, PR #303) · Files: `src/tradepartner/cli.py`, `pyproject.toml` · Depends on: T18, T21a, T11h; and #172 merged (not enforced by `team.py`)
- [x] **T20: `Broker` interface and fake broker.** (#23, PR #27) · Files: `src/tradepartner/adapters/{broker,fake_broker}.py` · Depends on: T4
- [x] **T21a: Dashboard shell.** (#66, PR #67) · Files: `src/tradepartner/dashboard/{__init__,app}.py` · Depends on: T4
- [x] **T21: Data-health page.** (#205, PR #207) · Files: `src/tradepartner/dashboard/health_page.py`, `src/tradepartner/dashboard/app.py`, `src/tradepartner/dashboard/theme.py`, `.streamlit/config.toml` · Depends on: T18, T21a
- [ ] **T22: Scheduling runbook and unattended evidence.** Files: `docs/runbooks/scheduling.md` (launchd plist; PATH for `uv`; working dir and `.env`; sleep vs power-off; logs; TCC) · Tests: none; evidence = five consecutive scheduled `ok` runs, collected by the owner · Depends on: T19; and #173 merged (not enforced by `team.py`) · Review: safety-reviewer. Runs in parallel with T21.
- [ ] **T23: `data-validator` agent and close-out.** Files: `.claude/agents/data-validator.md`, `docs/ways-of-working/agents.md`, `docs/STATUS.md`, `CHANGELOG.md`, this plan · Tests: agent runs `health --check` and the suite on the owner's real store; PR includes that report, the T22 evidence, and the real-store 2020 universe check (non-empty, includes a later-delisted name) · Depends on: T21, T22 · Review: quant-auditor on the report.

**Parallel lanes** (separate worktrees, non-overlapping files): T1 → {T2, T4}; T2 → T3 (owner); T4 → {T5, T20, T21a}; T5 → T6 → {T7, T8}; T8 → T8b → T9; {T7, T8b} → T10; {T3, T9} → T11 → T11b → T11g → T11c → T11d → T11e → T11f → T11h; {T3, T7, T8b} → T12; {T9, T10} → T13 → {T14, T15}; {T11, T12} → T16 → T17; {T15, T17} → T18; {T18, T21a} → T21; {T18, T21a, T11h} → T19; T19 → T22; {T21, T22} → T23.

## Chains (for team claims)

Dependent tasks one team should keep, in order. A chain is a preference, not a lock: every task is still claimed one at a time with `scripts/team.py claim` ([teams.md](../ways-of-working/teams.md)). Chains that wait on another chain's head cannot start until it merges, so the ready frontier is narrow early in the phase; merge chain heads first.

| Chain | Tasks | Starts when |
|---|---|---|
| universe | T5 → T6 → T7 → T10 | T4 merged (T10 also waits for T8b from the master chain) |
| broker | T20 | T4 merged |
| master | T8 → T8b → T9 | T6 merged |
| parsers | T11 → T11b → T11g → T11c → T11d → T11e → T11f → T11h; T12 in parallel | T3 (owner) plus T9 for T11; T7 and T8b for T12 |
| rules | T13 → T14, T15 | T9 and T10 merged |
| ux | T21a → T21 | T4 merged (T21 also waits for T18 from the pipeline chain) |
| pipeline | T16 → T17 → T18 → T19 | T11 and T12 merged (T18 also needs T15; T19 also needs T21a, T11h and #172) |
| close-out | T22 → T23 | T19 and #173 merged (T23 also waits for T21) |
| fixes | open `size:S` issues with no `team:` label | any time |

## Verification

End to end, on a clean checkout:
1. `uv run pytest` green with no `.env` and no network, including truncation-invariance and broken-adapter tests.
2. With the owner's `.env`: `uv run tradepartner ingest --source all --backfill` completes with no `--since`, in one run or resumed per the owner's T11h decision (runtime, EDGAR request count and `edgar.cache_dir` size recorded, and the same for one follow-up daily run); a later `ingest --source all` after the settle delay shows `rows_added = 0` for Alpaca, with EDGAR deltas explained in the run row.
3. `uv run tradepartner health --check` exits 0; numbers match a direct DuckDB query pasted alongside.
4. `uv run tradepartner dashboard` shows the same (screenshot).
5. Real-store month-end universe inside the backfill window, non-empty with a later-delisted name (query pasted).
6. Five consecutive scheduled `ok` runs; `data-validator` report attached (T23).

## Rollback

The store is one file: delete it and re-run `ingest --backfill`. The launchd plist is installed by hand and unloaded with one command (runbook). Dependencies are removed by reverting T1. No migrations touch anything outside the store file.

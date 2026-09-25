# Plan: Data foundation (Phase 2)

**Spec:** [specs/data-foundation.md](../specs/data-foundation.md)  ·  **Status:** Draft

## Approach (short)

Build inside-out (the filing index is always scanned over full history; `--since` limits only bars, actions and facts): config and calendar; raw-fetch clients and the recorder (so the owner can record fixtures early); the store and its as-of API with the truncation-invariance harness; the fixture universe; fixture adapters and the security master; the no-look-ahead suite; parsers for the real sources over the owner's recordings; universe, gap, ingest, health, CLI, page, close-out. CI never touches the network: an autouse fixture disables sockets.

Design choices: DuckDB file, raw OHLCV, read-time adjustment ([ADR 0003](../decisions/0003-data-adapters-local-first.md), [0004](../decisions/0004-tooling-adopt-avoid.md)); universe rules as config with a guarded SIC setting ([ADR 0006](../decisions/0006-universe-and-cadence.md)); **Streamlit** for the page; **Typer** for the CLI; **httpx** for raw EDGAR fetches; `edgartools` only for cover-page iXBRL parsing. Typer, pydantic-settings and httpx are recorded here as additions to ADR 0004's adopt table via a dated amendment note appended in T1 (ADRs are append-only).

**All runtime dependencies are added in T1** so no later task touches `pyproject.toml`/`uv.lock` (except T19's `[project.scripts]`): `pydantic`, `pydantic-settings`, `exchange_calendars`, `typer`, `httpx`, `duckdb`, `pyarrow`, `polars`, `edgartools` (exact pin), `alpaca-py` (exact pin), `streamlit`. Per-module `ignore_missing_imports` set in T1.

Package layout: `src/tradepartner/{config.py, calendar.py, cli_record.py, store/{schema,db,asof,master,classify}.py, adapters/{__init__,prices,filings,broker,fixture_prices,fixture_filings,fake_broker,alpaca_raw,edgar_raw,alpaca_prices,edgar}.py, universe.py, gap.py, health.py, ingest.py, backfill.py, cli.py, dashboard/health_page.py}`; `tests/conftest.py` owns the fixture-store loader and the no-network fixture.

## Tasks

Each task = one branch = one PR (~≤400 lines; generated fixture CSVs excluded). Tasks with no shared files may run in parallel. "Owner" tasks need the owner's keys and are not run by `implementer`.

- [x] **T1: Config, dependencies, calendar.** Files: `src/tradepartner/{config,calendar}.py`, `pyproject.toml`, `uv.lock`, `.env.example`, `docs/decisions/0004-tooling-adopt-avoid.md` (append "Amendment 2026-xx-xx" note listing typer, pydantic-settings, httpx) · Tests: `tests/test_config.py` (every key has its default; guarded SIC pinned; `liquidity_rule_enabled` defaults false; `fill_price` defaults close; no `.env` → import and load succeed), `tests/test_calendar.py` (sessions, open/close timestamps, last session of month, last completed session as of a timestamp, month window, half day, holiday; no weekday logic) · Depends on: n/a · Review: quant-auditor.
- [x] **T2: Raw-fetch clients and the recorder.** Files: `src/tradepartner/adapters/{alpaca_raw,edgar_raw}.py` (thin: return raw JSON / SGML text / downloaded files; EDGAR User-Agent and ~10 req/s throttle from config), `src/tradepartner/cli_record.py` (`python -m tradepartner.cli_record`: fetches a fixed list of payloads for a few CIKs and symbols, scrubs, writes to `tests/fixtures/{alpaca,edgar}/`), `tests/test_fixture_scrub.py` (pattern-based: key shapes, emails, User-Agent shapes) · Tests: scrub test on an empty fixture dir passes; a smoke test of the clients is marked `network` and skipped in CI · Depends on: T1 · Review: safety-reviewer.
- [ ] **T3 (owner): Record fixtures and resolve source facts.** Files: `tests/fixtures/{alpaca,edgar}/*` (recorded), `docs/research/2026-xx-xx-free-data-terms.md`, `src/tradepartner/config.py` (set `liquidity_rule_enabled`, `execution.fill_price`, `ingest.max_missing_share`), `tests/test_config.py` (update the pinned defaults) · Tests: scrub test passes on the recordings · Depends on: T2 · Review: safety-reviewer. The owner runs the recorder with their keys and records: Alpaca terms and local-storage permission, history depth, open = auction or IEX, fractional-at-open behavior, actions depth, assets-endpoint ticker reuse; EDGAR rate limit; a backfill runtime estimate; and the resulting config values. Runs in parallel with T4–T10.
- [x] **T4: Store schema, db layer, shared test loader.** Files: `src/tradepartner/store/{__init__,schema,db}.py`, `src/tradepartner/adapters/__init__.py`, `tests/conftest.py` (fixture-store loader from CSV; **autouse no-network fixture**) · Tests: `tests/store/test_schema.py` (column and provenance constraints; read-only helper; lock-retry helper; chunk-scoped write connection) · Depends on: T1 · Review: quant-auditor.
- [x] **T5: Fixture universe (CSV) and generator.** Files: `scripts/make_fixture_universe.py`, `tests/fixtures/universe/*.csv`, `tests/fixtures/universe/README.md` · Tests: `tests/test_fixture_universe.py` (every case in spec req 13 present; content-equal regeneration) · Depends on: T4 · Review: quant-auditor.
- [ ] **T6: As-of primitives and truncation-invariance harness.** Files: `src/tradepartner/store/asof.py` (`prices_as_of`, `adjusted_prices_as_of(include_dividends)`, `facts_as_of`, `listings_as_of`; bare date raises), `tests/lookahead/{__init__,harness}.py` (truncate helper; probes just before and after every distinct `known_at`) · Tests: `tests/store/test_asof.py` (latest revision; bar revision; split before/after `known_at`; backfilled split; revised dividend; date input raises), `tests/lookahead/test_asof_invariance.py` · Depends on: T5 · Review: quant-auditor.
- [ ] **T7: `PriceSource` interface and fixture adapter.** Files: `src/tradepartner/adapters/{prices,fixture_prices}.py` · Tests: `tests/adapters/test_fixture_prices.py` (bars; actions with first-seen proxy and revision rule; resolution by `security_id`) · Depends on: T6 · Review: quant-auditor.
- [ ] **T8: `FilingSource` interface, fixture adapter, security master core.** Files: `src/tradepartner/adapters/{filings,fixture_filings}.py`, `src/tradepartner/store/master.py` (`securities_as_of` from earliest issuer filing over full history; listings with class title; `snapshot_static` columns per config; dual-class; benchmark seeding) · Tests: `tests/store/test_master.py` (earliest-filing rule; issuer-forms filter excludes a Form 4-only filer; static reliance; reused ticker; same-company ticker change; dual-class listings; benchmarks seeded) · Depends on: T6 · Review: quant-auditor. Runs in parallel with T7.
- [ ] **T8b: Delistings and transfers, derived at read time.** Files: `src/tradepartner/store/delistings.py` (`listings_as_of` end derivation; 25/25-NSE per class and exchange; transfer detection from rows known ≤ T) · Tests: `tests/store/test_delistings.py` (preferred-class Form 25 leaves common; 25-NSE; transfer once new listing known; delisted at a T between filing and new listing; clean merger end session) · Depends on: T8 · Review: quant-auditor.
- [ ] **T9: Security-type classification.** Files: `src/tradepartner/store/classify.py` · Tests: `tests/store/test_classify.py` (one test per rule incl. F-6; unclassifiable bucket; each row carries rule and the `known_at` of the filing used or fetch time) · Depends on: T8b · Review: quant-auditor.
- [ ] **T10: No-look-ahead suite and broken adapter.** Files: `tests/lookahead/{test_suite,broken_adapter}.py` · Tests: suite passes on fixture adapters; broken adapter's five violations each caught by the named check · Depends on: T7, T8b · Review: quant-auditor.
- [ ] **T11: EDGAR parsers.** Files: `src/tradepartner/adapters/edgar.py` (parsers over T3's recorded payloads: filing index, companies snapshot, company facts with `as_of_date` and class dimension, SGML header SIC, cover page via `edgartools`, Forms 25/25-NSE class and exchange) · Tests: `tests/adapters/test_edgar.py` (provenance and `known_at` per the master table) · Depends on: T3, T9 · Review: quant-auditor, safety-reviewer.
- [ ] **T12: Alpaca parsers.** Files: `src/tradepartner/adapters/alpaca_prices.py` · Tests: `tests/adapters/test_alpaca_prices.py` (raw closes; `known_at` per timing rule; actions proxy; resolution through master) · Depends on: T3, T7, T8b · Review: quant-auditor, safety-reviewer. Runs in parallel with T11.
- [ ] **T13: Universe rules.** Files: `src/tradepartner/universe.py`, `tests/lookahead/test_universe_invariance.py` · Tests: `tests/test_universe.py` (rules 1–8 in order; dual-class; split-adjusted cap with ex-date ≤ T only; future-ex-date split ignored; stale shares; liquidity flag off recorded; output records settings) · Depends on: T9, T10 · Review: quant-auditor.
- [ ] **T14: Universe config tests and literal check.** Files: `tests/test_universe_config.py`, `tests/test_no_literals.py` (AST check on `universe.py` and `gap.py`) · Tests: one override test per `universe.*` key · Depends on: T13 · Review: quant-auditor.
- [ ] **T15: Survivorship gap.** Files: `src/tradepartner/gap.py`, `tests/lookahead/test_gap_invariance.py` · Tests: `tests/test_gap.py` (formula; clean merger not missing; three side categories) · Depends on: T13 · Review: quant-auditor. Runs in parallel with T14.
- [ ] **T16: Single-session ingest.** Files: `src/tradepartner/ingest.py` · Tests: `tests/test_ingest.py` (idempotent re-run; staleness on holiday, weekend, pre-close, half day; stale → non-zero and unchanged; per-source atomic chunk; lock retry with a closing reader and with a persistent writer) · Depends on: T11, T12 · Review: quant-auditor, safety-reviewer. Runs in parallel with T13–T15.
- [ ] **T17: Backfill and resume.** Files: `src/tradepartner/backfill.py` · Tests: `tests/test_backfill.py` (monthly chunks per source; mid-chunk failure leaves earlier chunks; resume from `chunk_cursor`; lock released between chunks) · Depends on: T16 · Review: quant-auditor.
- [ ] **T18: Health metrics.** Files: `src/tradepartner/health.py` · Tests: `tests/test_health.py` (each metric on the fixture store incl. delisted names and static reliance; each integrity rule fails on an injected violation incl. bar-after-delisting; guarded SIC) · Depends on: T15, T17 · Review: quant-auditor.
- [ ] **T19: CLI.** Files: `src/tradepartner/cli.py`, `pyproject.toml` (`[project.scripts]` only) · Tests: `tests/test_cli.py` (`ingest`, `health --check` exit codes, `dashboard` launches, `export` writes Parquet per table) · Depends on: T18 · Review: safety-reviewer (no secrets in output).
- [x] **T20: `Broker` interface and fake broker.** Files: `src/tradepartner/adapters/{broker,fake_broker}.py` · Tests: `tests/adapters/test_fake_broker.py` · Depends on: T4 · Review: safety-reviewer. Runs in parallel with T5–T19.
- [ ] **T21: Data-health page.** Files: `src/tradepartner/dashboard/{__init__,health_page}.py` · Tests: `tests/dashboard/test_health_page.py` (headless render shows every metric; read-only connections; busy state when locked) · Depends on: T19 · Review: `/code-review`.
- [ ] **T22: Scheduling runbook and unattended evidence.** Files: `docs/runbooks/scheduling.md` (launchd plist; PATH for `uv`; working dir and `.env`; sleep vs power-off; logs; TCC) · Tests: none; evidence = five consecutive scheduled `ok` runs, collected by the owner · Depends on: T19 · Review: safety-reviewer. Runs in parallel with T21.
- [ ] **T23: `data-validator` agent and close-out.** Files: `.claude/agents/data-validator.md`, `docs/ways-of-working/agents.md`, `docs/STATUS.md`, `CHANGELOG.md`, this plan · Tests: agent runs `health --check` and the suite on the owner's real store; PR includes that report, the T22 evidence, and the real-store 2020 universe check (non-empty, includes a later-delisted name) · Depends on: T21, T22 · Review: quant-auditor on the report.

**Parallel lanes** (separate worktrees, non-overlapping files): T1 → {T2, T4}; T2 → T3 (owner); T4 → {T5, T20}; T5 → T6 → {T7, T8}; T8 → T8b → T9; {T7, T8b} → T10; {T3, T9} → T11; {T3, T7, T8b} → T12; {T9, T10} → T13 → {T14, T15}; {T11, T12} → T16 → T17; {T15, T17} → T18 → T19 → {T21, T22} → T23.

## Chains (for team claims)

Dependent tasks one team should keep, in order. A chain is a preference, not a lock: every task is still claimed one at a time with `scripts/team.py claim` ([teams.md](../ways-of-working/teams.md)). Chains that wait on another chain's head cannot start until it merges, so the ready frontier is narrow early in the phase; merge chain heads first.

| Chain | Tasks | Starts when |
|---|---|---|
| universe | T5 → T6 → T7 → T10 | T4 merged (T10 also waits for T8b from the master chain) |
| broker | T20 | T4 merged |
| master | T8 → T8b → T9 | T6 merged |
| parsers | T11, T12 (parallel) | T3 (owner) plus T9 for T11; T7 and T8b for T12 |
| rules | T13 → T14, T15 | T9 and T10 merged |
| pipeline | T16 → T17 → T18 → T19 | T11 and T12 merged (T18 also needs T15) |
| close-out | T21, T22 → T23 | T19 merged |
| fixes | open `size:S` issues with no `team:` label | any time |

## Verification

End to end, on a clean checkout:
1. `uv run pytest` green with no `.env` and no network, including truncation-invariance and broken-adapter tests.
2. With the owner's `.env`: `uv run tradepartner ingest --source all --backfill --since <T3 date>` completes (runtime recorded); a later `ingest --source alpaca` after the settle delay shows `rows_added = 0` (EDGAR deltas explained in the run row).
3. `uv run tradepartner health --check` exits 0; numbers match a direct DuckDB query pasted alongside.
4. `uv run tradepartner dashboard` shows the same (screenshot).
5. Real-store month-end universe inside the backfill window, non-empty with a later-delisted name (query pasted).
6. Five consecutive scheduled `ok` runs; `data-validator` report attached (T23).

## Rollback

The store is one file: delete it and re-run `ingest --backfill`. The launchd plist is installed by hand and unloaded with one command (runbook). Dependencies are removed by reverting T1. No migrations touch anything outside the store file.
